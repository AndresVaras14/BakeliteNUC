# -*- coding: utf-8 -*-
"""
Validación de acceso contra la API externa de INNTEK/ASEM.

Contrato: "Manual de Usuario API Inntek Bakelite 1.1.pdf". Reemplazo real de
validador.Validador para producción, con la misma firma pública (validar,
disponible, error_carga) descrita en ese módulo, así que se enchufa en main.py
sin tocar el controlador.

Esta API SOLO resuelve si un RUT está habilitado o no (estado 1/0 + motivo);
no reemplaza a la API propia de Bakelite (marcas, heartbeat, dispositivos:
config.API_BASE y sincronizador.py), que sigue aparte.

Flujo según el manual:
  1) Login (POST WS_ValidaUsuarioOVALASEM) con user/passwd/rut de la empresa
     principal -> api_key. El manual advierte que CADA login invalida el
     api_key anterior, así que no se pide uno por carnet: se cachea en memoria
     y solo se renueva cuando la consulta lo rechaza (401/403) o todavía no
     hay ninguno.
  2) Consulta (GET WS_ConsultaTrabajadorBakelite) con headers rut + api_key y
     el parámetro rut_trabajador (con puntos y guion) -> estado 1/0 + motivo.

El manual describe el login como "body de la petición", pero la captura de
Postman que lo acompaña lo manda como query params y así es como responde
200 OK: se replica lo que funciona en la captura, no la prosa.

Un Lock protege el api_key compartido: dos lectoras (entrada/salida) pueden
disparar una consulta al mismo tiempo y ambas lo usan y, si hace falta, lo
renuevan.
"""

import json
import logging
import threading
import urllib.parse
import urllib.request
import urllib.error

import config
from rut import formatea_rut
from validador import Resultado, MENSAJES

log = logging.getLogger("validador_api_externa")

# api.asem.cl está detrás de Cloudflare y bloquea el User-Agent por defecto de
# urllib ("Python-urllib/3.x") con un 403 (error 1010, "firma no permitida").
# Este endpoint lo construyó INNTEK específicamente para Bakelite; el header
# solo evita el filtro automático de bots, nada más.
_USER_AGENT = "BakeliteNUC/1.0 (+https://sopytec.cl)"


def _cargar_credenciales(ruta=None):
    ruta = ruta or config.ARCHIVO_CREDENCIALES_API_EXTERNA
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:  # noqa: BLE001
        log.error("No se pudo leer %s: %s", ruta, e)
        return "", "", ""
    return (str(d.get("user") or ""), str(d.get("passwd") or ""),
            str(d.get("rut_principal") or ""))


class ValidadorApiExterna:
    def __init__(self, ruta_credenciales=None):
        self.user, self.passwd, self.rut_principal = _cargar_credenciales(ruta_credenciales)
        self.error_carga = None if (self.user and self.passwd and self.rut_principal) \
            else f"Faltan credenciales en {config.ARCHIVO_CREDENCIALES_API_EXTERNA}"
        self._api_key = None
        self._lock = threading.Lock()

    def disponible(self):
        """¿Hay credenciales cargadas? No implica que la API responda ya: eso
        se confirma recién con la primera consulta real (igual que con el
        JSON, que solo confirma que el archivo se pudo leer)."""
        return not self.error_carga

    # ---- Paso 1 del manual: obtener api_key ----
    def _autenticar(self):
        """Pide un api_key nuevo. Lanza la excepción tal cual; quien llama
        decide qué Resultado corresponde."""
        qs = urllib.parse.urlencode({
            "user": self.user, "passwd": self.passwd, "rut": self.rut_principal,
        })
        peticion = urllib.request.Request(
            f"{config.ASEM_ENDPOINT_LOGIN}?{qs}", method="POST",
            headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(
                peticion, timeout=config.ASEM_API_TIMEOUT_SEGUNDOS) as resp:
            cuerpo = json.loads(resp.read().decode("utf-8", "ignore"))
        if not cuerpo.get("status"):
            raise RuntimeError(f"La API externa rechazó el login: {cuerpo}")
        self._api_key = cuerpo["api_key"]
        log.info("Nuevo api_key obtenido de la API externa (cliente %s).",
                 cuerpo.get("cliente"))

    # ---- Paso 2 del manual: consultar el RUT ----
    def _consultar(self, rut_fmt, reintentar=True):
        if self._api_key is None:
            self._autenticar()
        qs = urllib.parse.urlencode({"rut_trabajador": rut_fmt})
        peticion = urllib.request.Request(
            f"{config.ASEM_ENDPOINT_CONSULTA}?{qs}",
            headers={"rut": self.rut_principal, "api_key": self._api_key,
                     "User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(
                    peticion, timeout=config.ASEM_API_TIMEOUT_SEGUNDOS) as resp:
                return json.loads(resp.read().decode("utf-8", "ignore"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403) and reintentar:
                # El api_key quedó inválido (venció, o alguien más pidió uno
                # nuevo y el manual dice que eso invalida el anterior): se
                # renueva una vez y se reintenta con el nuevo.
                log.warning("api_key rechazado (HTTP %s); se renueva.", e.code)
                self._api_key = None
                return self._consultar(rut_fmt, reintentar=False)
            raise

    # ---- Misma firma que Validador.validar ----
    def validar(self, rut_norm, sentido, simular_sin_conexion=False):
        if simular_sin_conexion:
            return Resultado(4, MENSAJES[4], False, sentido, rut_norm)
        if rut_norm == "0":
            return Resultado(3, MENSAJES[3], False, sentido, rut_norm)

        rut_fmt = formatea_rut(rut_norm)
        with self._lock:
            try:
                cuerpo = self._consultar(rut_fmt)
            except Exception as e:  # noqa: BLE001
                # Login caído, timeout, red caída, HTTP no-401/403: todo esto
                # es "no se pudo saber" -> amarillo (código 4), no rojo. Negar
                # el acceso por un problema nuestro de conexión sería peor que
                # pedir que se reintente.
                log.error("No se pudo consultar el RUT %s en la API externa: %s",
                          rut_norm, e)
                return Resultado(4, MENSAJES[4], False, sentido, rut_norm)

        habilitado = cuerpo.get("estado") == 1
        codigo = 1 if habilitado else 0
        return Resultado(
            codigo, MENSAJES[codigo], habilitado, sentido, rut_norm,
            rut_display=rut_fmt,
            nombre=cuerpo.get("nombre", ""),
            motivo="" if habilitado else (cuerpo.get("motivo") or "Acceso no habilitado"),
        )
