# -*- coding: utf-8 -*-
import datetime
import json
import logging
import time
import urllib.error
import urllib.request

import config
import terminal_auth

log = logging.getLogger("sincronizador")

class EstadoApiMixin:
    """Salud de API, conectividad y verificación del terminal."""

    @staticmethod
    def _api_viva(code):
        """¿Este código HTTP significa que la API está operativa?

        Solo si procesó la petición: 2xx, o un rechazo de validación/permiso.
        Un 404 significa que la ruta no está publicada —la API no está arriba—
        y 5xx/408/429 que el servicio no está sirviendo. Que el host conteste
        NO es lo mismo que que la API funcione.
        """
        if 200 <= code < 400:
            return True
        if code == 404 or code >= 500 or code in (408, 429):
            return False
        return True          # 400, 401, 403, 422...: la API respondió de verdad

    @staticmethod
    def _estado_bd(cuerpo):
        """Lee `baseDatos`/`estado` del health. Devuelve False si la API declara
        que su BD falla, True si declara que está bien, None si no informa nada."""
        try:
            d = json.loads(cuerpo)
        except Exception:  # noqa: BLE001
            return None
        if not isinstance(d, dict):
            return None
        bd = str(d.get("baseDatos", "")).upper()
        estado = str(d.get("estado", "")).upper()
        if bd == "OK" and estado in ("", "OK"):
            return True
        if bd or estado:
            # Informa algo distinto de OK: la API responde, pero no está sana.
            return False
        return None

    @staticmethod
    def _parse(cuerpo):
        try:
            d = json.loads(cuerpo)
            return d.get("idMarca"), d.get("estado")
        except Exception:  # noqa: BLE001
            return None, None

    @staticmethod
    def _leer(err):
        try:
            return err.read().decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            return ""

    def _ping(self):
        """Comprueba si BakeliteApi está realmente operativa.

        No sirve mirar solo si el host contesta: este servidor devuelve 404 para
        cualquier ruta cuando la API no está publicada, y un proxy delante de una
        API caída responde 502/503. Por eso se sondea el endpoint real:

            - 2xx o error de validación (400/401/403/422) -> la API está viva:
              recibió la petición y la procesó.
            - 404 -> la ruta no existe: la API no está publicada. CAÍDA.
            - 5xx, 408, 429, timeout, red -> CAÍDA.

        Con `config.ENDPOINT_ESTADO_API` definido se usa esa ruta
        URL con GET, que es más limpio y no toca el endpoint de marcas.
        """
        self._ultimo_ping = time.monotonic()
        if config.SIMULAR_API:
            self._marcar_online()
            return

        if config.ENDPOINT_ESTADO_API:
            # /health es público a propósito (lo sondea cualquiera cada 20 s,
            # sin terminal todavía dado de alta): no lleva X-Terminal-Key.
            url, datos, metodo = config.ENDPOINT_ESTADO_API, None, "GET"
            cabeceras = {}
        elif config.ENDPOINT_REGISTRAR_EVENTO:
            # Sonda sin efectos: cuerpo vacío, la API lo rechaza por validación
            # (400 sin terminal, o 401 si falta/erra la clave -- ambos cuentan
            # como "viva" en _api_viva).
            url, datos, metodo = config.ENDPOINT_REGISTRAR_EVENTO, b"{}", "POST"
            cabeceras = terminal_auth.cabeceras({"Content-Type": "application/json"})
        else:
            return

        req = urllib.request.Request(url, data=datos, method=metodo, headers=cabeceras)
        try:
            with urllib.request.urlopen(req, timeout=config.API_TIMEOUT_SEGUNDOS) as resp:
                code = resp.getcode()
                cuerpo = resp.read().decode("utf-8", "ignore")
            log.debug("Ping a %s: HTTP %s", url, code)

            # Con el endpoint /health, un 200 puede venir con la BD caída: la
            # API responde pero no podría guardar la marca. Eso cuenta como
            # sin conexión, para no gastar reintentos que van a fallar.
            estado_bd = self._estado_bd(cuerpo)
            if estado_bd is False:
                self._marcar_offline(f"La API responde pero su base de datos no: {cuerpo[:200]}")
                return
            self._marcar_online()
        except urllib.error.HTTPError as e:
            if self._api_viva(e.code):
                self._marcar_online()      # 400/401/403/422: procesó la petición
            elif e.code == 404:
                self._marcar_offline(f"HTTP 404 en {url}: la API no está publicada")
            else:
                self._marcar_offline(f"HTTP {e.code} en {url}")
        except Exception as e:  # noqa: BLE001
            self._marcar_offline(str(e))

    def _marcar_online(self):
        """El servicio respondió. Cuenta como comprobación fresca del estado. Si venía caído, cierra el incidente, lo deja
        anotado en Errores y lo encola para avisarle a BakeliteApi."""
        venia_caido = self.en_linea is False   # None (sin comprobar) no es una caída
        self.en_linea = True
        self._ultimo_ping = time.monotonic()
        self.ultima_conexion = datetime.datetime.now()

        if config.USAR_BD_LOCAL:
            self.bd_local.marcar_servicio("BAKELITE", True)
            if venia_caido or self._incidente_abierto:
                inc = self.bd_local.cerrar_incidente("BAKELITE")
                self._incidente_abierto = False
                if inc:
                    dur = inc.get("duracion_segundos") or 0
                    log.info("Conexión con BakeliteApi recuperada tras %s s (corte #%s).",
                             dur, inc["id"])
                    self.bd_local.registrar_error(
                        "api", "Conexión con BakeliteApi recuperada",
                        nivel="INFO",
                        detalle=(f"Corte #{inc['id']} detectado {inc['deteccion']}, "
                                 f"recuperado {inc['recuperacion']} "
                                 f"({dur} s, {inc['intentos_fallidos']} intentos fallidos)."))
        self._notificar_estado()

    def _marcar_offline(self, error=None):
        """El servicio no respondió. La primera vez abre el incidente y deja el
        error registrado; los siguientes fallos solo suman intentos."""
        primera_caida = self.en_linea is not False or not self._incidente_abierto
        self.en_linea = False
        self._ultimo_ping = time.monotonic()

        if config.USAR_BD_LOCAL:
            self.bd_local.marcar_servicio("BAKELITE", False, error=error)
            id_inc = self.bd_local.abrir_incidente("BAKELITE", error=error)
            if id_inc is not None and primera_caida and not self._incidente_abierto:
                log.error("Sin conexión con BakeliteApi (corte #%s): %s", id_inc, error)
                self.bd_local.registrar_error(
                    "api", "Sin conexión con BakeliteApi", nivel="ERROR",
                    detalle=f"Corte #{id_inc}. {error or ''}".strip())
            self._incidente_abierto = id_inc is not None
        self._notificar_estado()

    def _notificar_estado(self):
        if self.on_estado:
            try:
                self.on_estado(self.en_linea, self.ultima_conexion)
            except Exception as e:  # noqa: BLE001
                log.error("Error notificando estado en línea: %s", e)

    def verificar_terminal(self):
        """Consulta ENDPOINT_OBTENER_TERMINAL para comprobar que el terminal
        existe y está activo en Bakelite.

        El nombre no se toca aquí: se sincroniza en ambos sentidos por su propio
        ciclo (ver sincronizar_nombre y
        CONTRATO_SINCRONIZACION_NOMBRE_TERMINAL.md).
        """
        if not config.ENDPOINT_OBTENER_TERMINAL:
            return None
        url = config.ENDPOINT_OBTENER_TERMINAL.format(id=config.ID_TERMINAL)
        try:
            req = urllib.request.Request(url, method="GET", headers=terminal_auth.cabeceras())
            with urllib.request.urlopen(req, timeout=config.API_TIMEOUT_SEGUNDOS) as resp:
                datos = json.loads(resp.read().decode("utf-8", "ignore"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                msg = (f"El idTerminal {config.ID_TERMINAL} no existe en Bakelite: "
                       "las marcas serán rechazadas.")
                log.error(msg)
                if config.USAR_BD_LOCAL:
                    self.bd_local.registrar_error("config", msg, nivel="CRITICO")
            else:
                log.warning("No se pudo verificar el terminal (HTTP %s).", e.code)
            return None
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo verificar el terminal: %s", e)
            return None

        if not datos.get("activo", True):
            msg = f"El terminal {config.ID_TERMINAL} está INACTIVO en Bakelite."
            log.error(msg)
            if config.USAR_BD_LOCAL:
                self.bd_local.registrar_error("config", msg, nivel="ERROR")

        # El nombre viene en esta respuesta, pero es informativo: adoptarlo aquí
        # ignoraría las fechas y podría pisar un cambio local más nuevo.
        self.sincronizar_nombre(forzar=True)
        return datos

