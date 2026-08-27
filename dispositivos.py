# -*- coding: utf-8 -*-
"""
Sincronización de lectoras y relés con Bakelite.

Contrato: CONTRATO_DISPOSITIVOS_TERMINAL.md

Cada ciclo manda la foto completa de los dispositivos —cómo están configurados
y en qué estado están— y recibe de vuelta lo que en Bakelite sea más reciente.
Ese único viaje hace de comparación, subida y bajada.

Dos cosas viajan juntas pero se tratan distinto:

  - la CONFIGURACIÓN (qué dispositivo es ENTRADA o SALIDA) se sincroniza en
    ambos sentidos y gana el cambio más reciente; ante un empate exacto, gana
    este equipo, porque es el que tiene el cable enchufado;
  - el ESTADO (si está conectada, en qué puerto, cuándo leyó) solo lo puede
    observar este equipo, así que viaja en un solo sentido.

Sin conexión el terminal sigue operando con su configuración local: el control
de acceso no puede depender de que Bakelite responda. Lo que se cambie aquí
queda pendiente y sube al reconectar, siempre con su fecha original.
"""

import json
import time
import logging
import datetime
import threading
import urllib.request
import urllib.error
from uuid import uuid4

import config
from depurador import depurador

log = logging.getLogger("dispositivos")

# Cuántos 404 seguidos hacen falta para creer que el terminal de verdad no
# existe. Un servidor con la API sin publicar responde 404 a todo, y ese caso
# no es un error de configuración: es la API caída.
AVISAR_TRAS_404_SEGUIDOS = 3


def _iso(valor):
    """Fecha de la BD -> texto ISO 8601 con zona, como pide el contrato."""
    if valor is None:
        return None
    if isinstance(valor, datetime.datetime):
        if valor.tzinfo is None:
            valor = valor.astimezone()
        return valor.isoformat(timespec="seconds")
    return str(valor)


class SincronizadorDispositivos(threading.Thread):
    def __init__(self, bd_local, on_critico=None, on_cambio=None,
                 on_resuelto=None):
        super().__init__(daemon=True, name="Dispositivos")
        self.bd_local = bd_local
        self.on_critico = on_critico      # callback(texto) para avisarle al operador
        self.on_resuelto = on_resuelto    # callback() cuando el problema se corrigió
        self.on_cambio = on_cambio        # callback() cuando la config cambió desde la web
        self.intervalo = config.DISPOSITIVOS_INTERVALO_SEGUNDOS
        self.detenido_por_inactivo = False
        self._stop = threading.Event()
        self._ahora = threading.Event()   # despierta para mandar de inmediato
        self._fallos = 0
        self._404_seguidos = 0            # ver _es_config_rota()
        self._avisado = False             # ¿hay un cartel en pantalla?
        # El idCambio se crea UNA vez por tanda de cambios pendientes y se
        # reutiliza en cada reintento: es la clave con la que ambos lados
        # cruzan sus registros de lo mismo.
        self._id_cambio = None
        self._id_enviado = None      # el que viajó en el último envío

    def detener(self):
        self._stop.set()
        self._ahora.set()

    def notificar(self):
        """Algo cambió aquí —un sentido, una lectora que se enchufó— y no debe
        esperar al ciclo: la web tiene que verlo ya."""
        self._ahora.set()

    @property
    def url(self):
        return config.API_URL_DISPOSITIVOS.format(id=config.ID_TERMINAL)

    def run(self):
        while not self._stop.is_set():
            comienzo = time.monotonic()
            try:
                espera = self._sincronizar()
            except Exception as e:  # noqa: BLE001
                log.error("Error inesperado sincronizando dispositivos: %s", e)
                espera = self.intervalo
            if espera is None:            # 409: el terminal está inactivo
                return
            # Se descuenta lo que tardó la llamada, para que el ritmo sea de
            # verdad uno cada `intervalo` y no se acumulen envíos atrasados.
            resto = max(0.0, espera - (time.monotonic() - comienzo))
            self._ahora.wait(timeout=resto)
            self._ahora.clear()

    # ---- Un ciclo ----
    def _sincronizar(self):
        if not config.USAR_BD_LOCAL or not config.API_URL_DISPOSITIVOS:
            return self.intervalo
        lectoras = self.bd_local.lectoras()
        reles = self.bd_local.reles()
        if not lectoras and not reles:
            log.debug("Sin dispositivos en la BD local; no hay nada que sincronizar.")
            return self.intervalo

        pendientes = [d for d in lectoras + reles if not d["sincronizado"]]
        if pendientes and self._id_cambio is None:
            self._id_cambio = uuid4().hex
        # El idCambio va SIEMPRE. Que aquí no haya nada pendiente no significa
        # que allá no cambie nada: si el dispositivo no existe en Bakelite, este
        # envío lo da de alta, y eso también es un cambio de configuración que
        # la API exige poder correlacionar. Mientras haya pendientes locales se
        # reutiliza el mismo, para que los reintentos hablen del mismo hecho.
        id_cambio = self._id_cambio or uuid4().hex
        # Se guarda el que se usó de verdad: los registros de ambos lados se
        # cruzan por este id, y anotar None ahí lo dejaría inservible.
        self._id_enviado = id_cambio
        cuerpo = self._armar(lectoras, reles, id_cambio)

        try:
            peticion = urllib.request.Request(
                self.url, data=json.dumps(cuerpo, ensure_ascii=False).encode("utf-8"),
                method="POST", headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(
                    peticion, timeout=config.DISPOSITIVOS_TIMEOUT_SEGUNDOS) as resp:
                datos = json.loads(resp.read().decode("utf-8", "ignore") or "{}")
        except urllib.error.HTTPError as e:
            return self._rechazado(e)
        except Exception as e:  # noqa: BLE001
            # Fallo temporal: el terminal sigue operando con lo suyo.
            self._fallos += 1
            if self._fallos == 1 or self._fallos % 30 == 0:
                log.warning("Sin poder sincronizar dispositivos (%d seguidos): %s",
                            self._fallos, e)
            return self.intervalo

        return self._aceptado(datos, pendientes)

    def _armar(self, lectoras, reles, id_cambio):
        term = self.bd_local.terminal() or {}
        # Un relé no tiene puerto propio: lo acciona el Arduino. Su conexión ES
        # la del Arduino, así que se deriva en vez de guardarse aparte, que solo
        # daría lugar a que las dos copias se desincronicen. Sin este campo la
        # web mostraba los relés como "Sin estado" para siempre.
        arduino_ok = bool(term.get("arduino_conectado"))
        cuerpo = {
            "idTerminal": config.ID_TERMINAL,
            "lectoras": [{
                "numero": l["numero"], "sentido": l["sentido"],
                "descripcion": l["descripcion"], "activa": l["activa"],
                "configFecha": _iso(l["config_fecha"]), "configPor": l["config_por"],
                "conectada": l["conectada"], "puerto": l["puerto"],
                "ultimaLectura": _iso(l["ultima_lectura"]),
                "ultimoError": l["ultimo_error"],
            } for l in lectoras],
            "reles": [{
                "numero": r["numero"], "sentido": r["sentido"],
                "comando": r["comando"], "descripcion": r["descripcion"],
                "activo": r["activo"], "configFecha": _iso(r["config_fecha"]),
                "configPor": r["config_por"], "conectado": arduino_ok,
                "ultimoDisparo": _iso(r["ultimo_disparo"]),
                "ultimoError": r["ultimo_error"],
            } for r in reles],
            "arduino": {"conectado": bool(term.get("arduino_conectado")),
                        "puerto": term.get("arduino_puerto")},
        }
        cuerpo["idCambio"] = id_cambio
        return cuerpo

    def _aceptado(self, datos, pendientes):
        if self._fallos:
            log.info("Sincronización de dispositivos restablecida tras %d fallos.",
                     self._fallos)
        self._fallos = 0
        self._resolver_aviso()

        intervalo = datos.get("sincronizarCadaSegundos")
        if isinstance(intervalo, (int, float)) and intervalo > 0:
            if intervalo != self.intervalo:
                log.info("La API pide sincronizar dispositivos cada %s s.", intervalo)
            self.intervalo = float(intervalo)

        self._aplicar_remoto(datos.get("aplicar") or {})
        self._marcar_resultados(datos.get("resultados") or [], pendientes)
        return self.intervalo

    def _aplicar_remoto(self, aplicar):
        """Adopta lo que en Bakelite es más reciente. Cada dispositivo por
        separado: puede que allá cambiaran una lectora y aquí un relé."""
        cambio = False
        for tipo, clave in (("lectora", "lectoras"), ("rele", "reles")):
            for d in aplicar.get(clave) or []:
                activo = d.get("activa" if tipo == "lectora" else "activo")
                ok = self.bd_local.aplicar_config_remota(
                    tipo, d.get("numero"), d.get("sentido"), d.get("configFecha"),
                    descripcion=d.get("descripcion"), activo=activo,
                    config_por=d.get("configPor"))
                id_cambio = d.get("idCambio")
                if ok:
                    cambio = True
                    texto = (f"{tipo.capitalize()} {d.get('numero')} pasa a "
                             f"{'ENTRADA' if d.get('sentido') == 'E' else 'SALIDA'} "
                             f"por cambio en Bakelite (idCambio {id_cambio}, "
                             f"por {d.get('configPor')})")
                    log.info(texto)
                    depurador.respuesta(texto, origen="dispositivos")
                    self._registrar("dispositivos", texto, nivel="INFO")
                else:
                    # La guarda de la BD lo rechazó: aquí hay algo más nuevo que
                    # todavía no subió. Se resuelve solo en el ciclo siguiente.
                    log.info("No se adopta %s %s: la configuración local es más "
                             "nueva (idCambio %s).", tipo, d.get("numero"), id_cambio)
        if cambio and self.on_cambio:
            try:
                self.on_cambio()
            except Exception as e:  # noqa: BLE001
                log.error("Error avisando el cambio de dispositivos: %s", e)

    def _marcar_resultados(self, resultados, pendientes):
        """Lo que la API aceptó deja de estar pendiente."""
        subidos = {"lectora": [], "rele": []}
        rechazos = 0
        for r in resultados:
            estado, tipo, numero = r.get("estado"), r.get("tipo"), r.get("numero")
            if estado in ("ACTUALIZADO", "SIN_CAMBIOS", "CREADO"):
                subidos.setdefault(tipo, []).append(numero)
                if estado != "SIN_CAMBIOS":
                    texto = (f"{tipo} {numero} {estado.lower()} en Bakelite "
                             f"(idCambio {self._id_enviado})")
                    log.info(texto)
                    depurador.accion(texto, origen="dispositivos")
            elif estado == "RECHAZADO_POR_ANTIGUEDAD":
                # No es un error: allá había algo más nuevo y ya vino en
                # `aplicar`. Se registra porque es un conflicto resuelto.
                rechazos += 1
                texto = (f"{tipo} {numero}: Bakelite tenía una configuración más "
                         f"nueva; se adopta la suya (idCambio {self._id_enviado})")
                log.info(texto)
                self._registrar("dispositivos", texto, nivel="WARN")
            else:
                log.error("Estado desconocido para %s %s: %r", tipo, numero, estado)

        for tipo, numeros in subidos.items():
            if numeros:
                self.bd_local.marcar_dispositivos_sincronizados(tipo, numeros)

        # La tanda se cerró: el próximo cambio empieza su propio idCambio.
        if pendientes and not rechazos:
            self._id_cambio = None
        elif rechazos:
            self._id_cambio = None

    # ---- Errores ----
    def _rechazado(self, error):
        codigo = error.code
        detalle = self._leer(error)

        if codigo == 409:
            texto = (f"El terminal {config.ID_TERMINAL} está INACTIVO en Bakelite: "
                     "se dejó de sincronizar la configuración de dispositivos.")
            log.error(texto)
            self.detenido_por_inactivo = True
            self._registrar("config", texto, nivel="CRITICO", detalle=detalle)
            self._avisar(texto)
            return None

        if codigo == 404:
            texto = (f"El idTerminal {config.ID_TERMINAL} no existe en Bakelite: "
                     "no se pueden sincronizar los dispositivos.")
            self._404_seguidos += 1
            if self._404_seguidos == 1:
                log.error(texto)
                self._registrar("config", texto, nivel="CRITICO", detalle=detalle)
            if self._es_config_rota() and not self._avisado:
                # Se repitió lo suficiente: ahora sí es un problema de
                # configuración y el operador tiene que verlo.
                self._avisar(texto)
            self._fallos += 1
            return config.DISPOSITIVOS_ESPERA_ERROR_SEGUNDOS

        if codigo == 400:
            # Datos inválidos: reintentar el mismo cuerpo daría lo mismo. Se
            # registra para revisión y se espacia el reintento.
            texto = f"Bakelite rechazó la configuración de dispositivos: {detalle[:300]}"
            if self._fallos == 0:
                log.error(texto)
                self._registrar("dispositivos", texto, nivel="ERROR")
            self._fallos += 1
            return config.DISPOSITIVOS_ESPERA_ERROR_SEGUNDOS

        if codigo == 429:
            espera = self._retry_after(error) or config.DISPOSITIVOS_ESPERA_ERROR_SEGUNDOS
            log.warning("Límite de peticiones en dispositivos; se reintenta en %s s.",
                        espera)
            return espera

        self._fallos += 1
        if self._fallos == 1 or self._fallos % 30 == 0:
            log.warning("Dispositivos devolvió HTTP %s (%d seguidos).",
                        codigo, self._fallos)
        return self.intervalo

    # ---- Auxiliares ----
    @staticmethod
    def _retry_after(error):
        try:
            return max(1.0, float(error.headers.get("Retry-After")))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _leer(error):
        try:
            return error.read().decode("utf-8", "ignore")[:500]
        except Exception:  # noqa: BLE001
            return ""

    def _es_config_rota(self):
        """¿Este 404 es de verdad un terminal inexistente?

        Un 404 no siempre significa eso. Cuando la API no está publicada, el
        servidor responde 404 a CUALQUIER ruta, y al arrancar sin conexión el
        terminal mostraba en pantalla "el idTerminal no existe" — un error de
        configuración que no era tal, y que además no se iba solo al volver la
        API. Se exige que se repita para creerle.
        """
        return self._404_seguidos >= AVISAR_TRAS_404_SEGUIDOS

    def _resolver_aviso(self):
        """La API respondió: si había un cartel en pantalla, se retira."""
        self._404_seguidos = 0
        if self._avisado:
            self._avisado = False
            if self.on_resuelto:
                try:
                    self.on_resuelto()
                except Exception as e:  # noqa: BLE001
                    log.error("Error retirando el aviso: %s", e)

    def _avisar(self, texto):
        self._avisado = True
        if self.on_critico:
            try:
                self.on_critico(texto)
            except Exception as e:  # noqa: BLE001
                log.error("Error avisando al operador: %s", e)

    def _registrar(self, origen, mensaje, nivel="ERROR", detalle=None):
        if self.bd_local is None or not config.USAR_BD_LOCAL:
            return
        try:
            self.bd_local.registrar_error(origen, mensaje, nivel=nivel, detalle=detalle)
        except Exception as e:  # noqa: BLE001
            log.error("No se pudo registrar el evento de dispositivos: %s", e)
