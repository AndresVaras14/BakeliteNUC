# -*- coding: utf-8 -*-
"""
Sincronizador: sube cada marca pendiente a la BD local (SQL Server) y a la API de
Bakelite, y reintenta las que queden pendientes. Cumple el contrato de
integración (CONTRATO_INTEGRACION_TORNIQUETE.md):

  - POST config.ENDPOINT_REGISTRAR_EVENTO  (Content-Type JSON)
  - Envía el payload EXACTO guardado en la cola local (mismo idEvento en reintentos).
  - HTTP 201 REGISTRADO o HTTP 200 DUPLICADO -> entregado (se marca subido_api=1).
  - HTTP 400 -> datos inválidos: no se reintenta (subido_api=-1), se guarda el detalle.
  - Timeout / red / 429 / 5xx -> permanece pendiente y se reintenta con espera
    incremental (tope 60 s).

Cada intento contra la API —con su respuesta o su error— queda escrito en la BD
local (tor.EnviosApi), de modo que siempre se puede saber qué información llegó
a BakeliteApi y cuál sigue pendiente de enviar.

También mantiene el estado "en línea" y la hora de la última conexión al servidor.
"""

import json
import time
import logging
import threading
import datetime
import urllib.request
import urllib.error

import config

log = logging.getLogger("sincronizador")


class Sincronizador(threading.Thread):
    def __init__(self, store, bd_local, on_estado=None, on_nombre=None):
        super().__init__(daemon=True, name="Sincronizador")
        self.store = store
        self.bd_local = bd_local
        self.on_estado = on_estado          # callback(en_linea: bool, ultima: datetime|None)
        self.on_nombre = on_nombre          # callback(nombre: str) tras sincronizarlo
        self.en_linea = None        # None = todavía no se comprueba
        self.ultima_conexion = None
        self._incidente_abierto = False
        self._ultimo_ping = 0.0
        self._ultimo_nombre_sync = 0.0
        # Un 400 o un 404 en el nombre no se reintenta en bucle: se corta hasta
        # el próximo arranque, cuando la configuración pudo haberse corregido.
        self._nombre_bloqueado = False
        # No llamar a este atributo ``_stop``: Thread.join() usa internamente
        # un método con ese nombre y dejaría de poder esperar el cierre.
        self._detener_evento = threading.Event()
        self._restaurar_estado()
        self._wake = threading.Event()

    def _restaurar_estado(self):
        """Al arrancar, recupera de la BD la última conexión conocida y si
        quedó un corte abierto, para que la pantalla no parta en blanco."""
        if not config.USAR_BD_LOCAL:
            return
        estado = self.bd_local.estado_servicio("BAKELITE")
        if estado:
            self.ultima_conexion = estado.get("ultima_conexion")
        self._incidente_abierto = self.bd_local.incidente_abierto("BAKELITE") is not None

    def detener(self):
        self._detener_evento.set()
        self._wake.set()

    def notificar(self):
        """Despierta al sincronizador para subir de inmediato (tras una marca)."""
        self._wake.set()

    def run(self):
        # Lo primero es comprobar de verdad si hay conexión. Hasta que esta
        # respuesta llega, la pantalla muestra "verificando…" y no "sin conexión".
        try:
            self._ping()
        except Exception as e:  # noqa: BLE001
            log.error("Error en la comprobación inicial de conexión: %s", e)

        espera = config.SINCRONIZAR_INTERVALO_SEGUNDOS
        while not self._detener_evento.is_set():
            try:
                hubo_fallo_red = self._ciclo()
            except Exception as e:  # noqa: BLE001
                log.error("Error en ciclo de sincronización: %s", e)
                hubo_fallo_red = True
            # Espera incremental ante fallos de comunicación; se reinicia al conectar.
            if hubo_fallo_red:
                espera = min(espera * 2, config.SINCRONIZAR_ESPERA_MAX_SEGUNDOS)
            else:
                espera = config.SINCRONIZAR_INTERVALO_SEGUNDOS
            self._wake.wait(timeout=espera)
            self._wake.clear()

    def _ciclo(self):
        pendientes = self.store.pendientes()
        if not pendientes:
            self._ping()               # sin nada que subir, solo verifica conexión
            if self.en_linea:
                self._avisar_incidentes()
                self._sincronizar_nombre_si_toca()
            return False

        # Con cola pendiente, el estado se refresca igual cada
        # PING_INTERVALO_SEGUNDOS: si los reintentos se espaciaron por la espera
        # incremental, el indicador no puede quedarse con la última foto.
        if (time.monotonic() - self._ultimo_ping) >= config.PING_INTERVALO_SEGUNDOS:
            self._ping()

        # 1) BD local: SIEMPRE, para todas (es local y rápido; ninguna marca
        #    puede quedar bloqueada detrás de otra que falle en la API).
        for ev in pendientes:
            if not ev.get("subido_local"):
                id_marca = self._subir_local(ev)
                if id_marca is not None:
                    ev["id_marca_local"] = id_marca
                    self.store.marcar(ev["id"], local=True,
                                      extra={"id_marca_local": id_marca})

        # 2) API: intenta cada marca pendiente. Un error del servidor (500/429)
        #    en una NO impide intentar las demás. Solo se corta el ciclo si el
        #    servidor está inalcanzable (red caída), para no encadenar timeouts.
        hubo_fallo_red = False
        for ev in pendientes:
            if ev.get("subido_api"):
                continue
            estado = self._subir_api(ev)
            if estado == "sin_conexion":
                hubo_fallo_red = True
                break
            if estado == "reintentar":
                hubo_fallo_red = True

        if self.en_linea:
            self._avisar_incidentes()
            self._sincronizar_nombre_si_toca()
        return hubo_fallo_red

    # ---- BD local (SQL Server) ----
    def _subir_local(self, ev):
        """Normalmente la marca ya está en la BD: la creó el controlador al
        pasar la cédula. Esto cubre el caso contrario —BD caída en ese momento—
        reconstruyéndola con el mismo IdEvento. Devuelve el IdMarca o None."""
        if not config.USAR_BD_LOCAL:
            return None
        id_marca = ev.get("id_marca_local") or self.bd_local.recuperar_marca(ev)
        if id_marca is None:
            log.warning("Marca %s no se pudo guardar en la BD local; queda pendiente.",
                        ev.get("id_evento"))
        return id_marca

    # ---- API (contrato) ----
    def _subir_api(self, ev):
        """Devuelve 'ok', 'permanente' o 'reintentar'. Marca la cola y deja
        el intento registrado en la BD local (tor.EnviosApi)."""
        if config.SIMULAR_API:
            self.store.marcar(ev["id"], api=True)
            self._registrar_envio(ev, "OK", estado_api="SIMULADO")
            self._marcar_online()
            return "ok"
        if not config.ENDPOINT_REGISTRAR_EVENTO:
            return "reintentar"

        payload = ev.get("payload")
        if not payload:
            self.store.marcar(ev["id"], api=-1, extra={"api_error": "sin payload"})
            self._registrar_envio(ev, "PERMANENTE", mensaje_error="sin payload")
            return "permanente"

        cuerpo_envio = json.dumps(payload, ensure_ascii=False)
        req = urllib.request.Request(
            config.ENDPOINT_REGISTRAR_EVENTO,
            data=cuerpo_envio.encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json"})
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=config.API_TIMEOUT_SEGUNDOS) as resp:
                code = resp.getcode()
                cuerpo = resp.read().decode("utf-8", "ignore")
            ms = int((time.monotonic() - t0) * 1000)
            self._marcar_online()
            idmarca, estado = self._parse(cuerpo)
            self.store.marcar(ev["id"], api=True, extra={"idMarca": idmarca})
            self._registrar_envio(ev, "OK", http_status=code, request_json=cuerpo_envio,
                                  respuesta_json=cuerpo, estado_api=estado,
                                  id_marca_api=idmarca, duracion_ms=ms)
            log.info("Marca %s entregada (HTTP %s, %s) idMarca=%s",
                     ev["id_evento"], code, estado, idmarca)
            return "ok"
        except urllib.error.HTTPError as e:
            ms = int((time.monotonic() - t0) * 1000)
            detalle = self._leer(e)
            # Mismo criterio que el ping: un 404 o un 5xx aquí es la API caída,
            # no "el servidor contestó algo". Si no, cada reintento de una marca
            # pendiente contra una API caída la daba por recuperada.
            if self._api_viva(e.code):
                self._marcar_online()
            else:
                self._marcar_offline(f"HTTP {e.code} al enviar la marca")
            if e.code == 400:
                self.store.marcar(ev["id"], api=-1,
                                  extra={"api_error": f"HTTP 400: {detalle[:300]}"})
                self._registrar_envio(ev, "PERMANENTE", http_status=e.code,
                                      request_json=cuerpo_envio, respuesta_json=detalle,
                                      mensaje_error=f"HTTP 400: {detalle[:300]}",
                                      duracion_ms=ms)
                self._registrar_error("api", f"Marca {ev['id_evento']} rechazada (400)",
                                      detalle=detalle)
                log.error("Marca %s rechazada por la API (400): %s",
                          ev["id_evento"], detalle[:300])
                return "permanente"
            # 429 / 5xx / otros -> reintentar
            self._registrar_envio(ev, "REINTENTAR", http_status=e.code,
                                  request_json=cuerpo_envio, respuesta_json=detalle,
                                  mensaje_error=f"HTTP {e.code}: {detalle[:300]}",
                                  duracion_ms=ms)
            log.warning("Marca %s: HTTP %s, se reintenta. %s",
                        ev["id_evento"], e.code, detalle[:200])
            return "reintentar"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            ms = int((time.monotonic() - t0) * 1000)
            self._marcar_offline(str(e))   # timeout / red -> servidor inalcanzable
            # HttpStatus queda NULL: ni siquiera hubo respuesta del servidor.
            self._registrar_envio(ev, "REINTENTAR", request_json=cuerpo_envio,
                                  mensaje_error=f"Fallo de red: {e}", duracion_ms=ms)
            log.warning("Marca %s: fallo de red (%s), se reintenta", ev["id_evento"], e)
            return "sin_conexion"

    # ---- Bitácora en la BD local ----
    def _registrar_envio(self, ev, clasificacion, **kw):
        """Anota el intento en tor.EnviosApi. Si la marca aún no llegó a la BD
        local (BD caída), no hay dónde colgarlo: se omite sin romper el ciclo."""
        if not config.USAR_BD_LOCAL:
            return
        id_marca = ev.get("id_marca_local")
        if id_marca is None:
            id_marca = self.bd_local.id_marca_por_evento(ev.get("id_evento"))
            if id_marca is None:
                return
            ev["id_marca_local"] = id_marca
            self.store.marcar(ev["id"], extra={"id_marca_local": id_marca})
        self.bd_local.registrar_envio_bakelite(id_marca, clasificacion, **kw)

    def _registrar_error(self, origen, mensaje, detalle=None, nivel="ERROR"):
        if config.USAR_BD_LOCAL:
            self.bd_local.registrar_error(origen, mensaje, nivel=nivel, detalle=detalle)

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

    # ---- Estado en línea ----
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
            url, datos, metodo = config.ENDPOINT_ESTADO_API, None, "GET"
            cabeceras = {}
        elif config.ENDPOINT_REGISTRAR_EVENTO:
            # Sonda sin efectos: cuerpo vacío, la API lo rechaza por validación.
            url, datos, metodo = config.ENDPOINT_REGISTRAR_EVENTO, b"{}", "POST"
            cabeceras = {"Content-Type": "application/json"}
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

    # ---- Verificación del terminal ----
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
            req = urllib.request.Request(url, method="GET")
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

    # ---- Sincronización del nombre del terminal ----
    # CONTRATO_SINCRONIZACION_NOMBRE_TERMINAL.md: el nombre se cambia tanto aquí
    # como en la web de Bakelite y gana el cambio más reciente. Cada lado guarda
    # la hora exacta del suyo (NombreFecha) y esa hora decide.
    @staticmethod
    def _url_nombre(plantilla):
        return plantilla.format(id=config.ID_TERMINAL) if plantilla else None

    def _sincronizar_nombre_si_toca(self):
        """Ritmo propio, mucho más lento que el de las marcas: el nombre no
        cambia seguido y la comparación no escribe nada."""
        if (time.monotonic() - self._ultimo_nombre_sync) < config.NOMBRE_SYNC_INTERVALO_SEGUNDOS:
            return
        self.sincronizar_nombre()

    def sincronizar_nombre(self, forzar=False):
        """Deja el nombre igual en la BD local y en Bakelite.

        Si hay un cambio local sin subir va directo al PUT; si no, compara y
        actúa según el veredicto. Devuelve el nombre vigente, o None si no se
        pudo resolver (sin red, sin BD o bloqueado por un error definitivo).
        """
        if not config.USAR_BD_LOCAL or self._nombre_bloqueado:
            return None
        if not (config.ENDPOINT_COMPARAR_NOMBRE_TERMINAL
                and config.ENDPOINT_OBTENER_NOMBRE_TERMINAL
                and config.ENDPOINT_ACTUALIZAR_NOMBRE_TERMINAL):
            return None
        if not forzar and self.en_linea is False:
            return None            # sin red: el nombre local manda en pantalla

        term = self.bd_local.terminal()
        if not term:
            log.warning("No se pudo leer el terminal de la BD local; nombre sin sincronizar.")
            return None
        self._ultimo_nombre_sync = time.monotonic()

        # Un cambio local pendiente no necesita comparar: el PUT ya resuelve el
        # conflicto en una sola llamada (lo aplica o devuelve el nombre vigente).
        if not term.get("nombre_sincronizado"):
            return self._subir_nombre(term)

        veredicto = self._comparar_nombre(term)
        if veredicto == "ACTUALIZAR_LOCAL":
            return self._bajar_nombre()
        if veredicto == "ACTUALIZAR_API":
            return self._subir_nombre(term)
        if veredicto == "IGUALES":
            return term.get("nombre")
        return None

    def _comparar_nombre(self, term):
        """POST .../comparar — no escribe en ningún lado. Devuelve el veredicto."""
        fecha = _iso(term.get("nombre_fecha"))
        if not fecha:
            log.error("El terminal local no tiene NombreFecha; no se puede comparar.")
            return None
        payload = {"nombre": term.get("nombre"), "nombreFecha": fecha,
                   "nombreOrigen": "LOCAL"}
        code, cuerpo = self._llamar_nombre(
            "POST", self._url_nombre(config.ENDPOINT_COMPARAR_NOMBRE_TERMINAL), payload)
        if code != 200:
            return None
        try:
            d = json.loads(cuerpo)
        except Exception as e:  # noqa: BLE001
            log.error("Respuesta ilegible al comparar el nombre: %s", e)
            return None
        veredicto = d.get("veredicto")
        if veredicto not in ("IGUALES", "ACTUALIZAR_LOCAL", "ACTUALIZAR_API"):
            log.error("Veredicto desconocido al comparar el nombre: %r", veredicto)
            return None
        if veredicto != "IGUALES":
            log.info("Nombre del terminal — local: %r · Bakelite: %r → %s",
                     (d.get("local") or {}).get("nombre"),
                     (d.get("api") or {}).get("nombre"), veredicto)
        elif not term.get("nombre_sincronizado"):
            self.bd_local.marcar_nombre_sincronizado()
        return veredicto

    def _bajar_nombre(self):
        """GET .../hacia-nuc — adopta el nombre de Bakelite con SU fecha."""
        code, cuerpo = self._llamar_nombre(
            "GET", self._url_nombre(config.ENDPOINT_OBTENER_NOMBRE_TERMINAL))
        if code != 200:
            return None
        try:
            d = json.loads(cuerpo)
        except Exception as e:  # noqa: BLE001
            log.error("Respuesta ilegible al bajar el nombre: %s", e)
            return None
        if self.bd_local.aplicar_nombre_remoto(d.get("nombre"), d.get("nombreFecha"),
                                               d.get("nombrePor")):
            self._notificar_nombre(d.get("nombre"))
            return d.get("nombre")
        # El UPDATE no tocó nada: el nombre local ya era más nuevo. Lo resuelve
        # el PUT del ciclo siguiente; no se pisa nada.
        log.info("El nombre local es más nuevo que el de Bakelite: no se adopta.")
        return None

    def _subir_nombre(self, term):
        """PUT .../desde-nuc — sube el nombre local con la fecha de su cambio."""
        fecha = _iso(term.get("nombre_fecha"))
        if not fecha:
            log.error("El terminal local no tiene NombreFecha; no se puede subir.")
            return None
        payload = {"nombre": term.get("nombre"), "nombreFecha": fecha,
                   "nombrePor": term.get("nombre_por")}
        code, cuerpo = self._llamar_nombre(
            "PUT", self._url_nombre(config.ENDPOINT_ACTUALIZAR_NOMBRE_TERMINAL), payload)
        if code != 200:
            return None
        try:
            d = json.loads(cuerpo)
        except Exception as e:  # noqa: BLE001
            log.error("Respuesta ilegible al subir el nombre: %s", e)
            return None

        estado = d.get("estado")
        if estado == "RECHAZADO_POR_ANTIGUEDAD":
            # Perdió la carrera: la API devuelve el nombre vigente y se adopta
            # aquí mismo. El conflicto queda cerrado sin una segunda vuelta.
            log.info("El cambio local perdió la carrera; se adopta %r de Bakelite.",
                     d.get("nombre"))
            self.bd_local.aplicar_nombre_remoto(d.get("nombre"), d.get("nombreFecha"))
            self._notificar_nombre(d.get("nombre"))
            return d.get("nombre")

        if estado in ("ACTUALIZADO", "SIN_CAMBIOS"):
            self.bd_local.marcar_nombre_sincronizado()
            if estado == "ACTUALIZADO":
                log.info("Nombre %r subido a Bakelite.", d.get("nombre"))
            return d.get("nombre")

        log.error("Estado desconocido al subir el nombre: %r", estado)
        return None

    def _llamar_nombre(self, metodo, url, payload=None):
        """Las tres llamadas del contrato comparten manejo de errores.

        Devuelve (código, cuerpo). El código es None si no hubo respuesta. Un
        400 o un 404 son definitivos: se bloquea el ciclo del nombre hasta el
        próximo arranque en vez de reintentar en bucle. El resto (429, 5xx,
        timeout, red) deja todo pendiente para el ciclo siguiente.
        """
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
        cabeceras = {"Content-Type": "application/json"} if payload else {}
        req = urllib.request.Request(url, data=data, method=metodo, headers=cabeceras)
        try:
            with urllib.request.urlopen(req, timeout=config.API_TIMEOUT_SEGUNDOS) as resp:
                return resp.getcode(), resp.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            detalle = self._leer(e)
            if e.code == 404:
                msg = (f"El idTerminal {config.ID_TERMINAL} no existe en Bakelite: "
                       "el nombre no se puede sincronizar.")
                log.error(msg)
                self.bd_local.registrar_error("config", msg, nivel="CRITICO",
                                              detalle=detalle[:500])
                self._nombre_bloqueado = True
            elif e.code == 400:
                msg = f"Bakelite rechazó el nombre del terminal (400): {detalle[:300]}"
                log.error(msg)
                self.bd_local.registrar_error("config", msg, nivel="ERROR")
                self._nombre_bloqueado = True
            else:
                log.warning("HTTP %s sincronizando el nombre (%s %s).",
                            e.code, metodo, url)
            return e.code, detalle
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo sincronizar el nombre (%s %s): %s", metodo, url, e)
            return None, ""

    def _notificar_nombre(self, nombre):
        if self.on_nombre and nombre:
            try:
                self.on_nombre(nombre)
            except Exception as e:  # noqa: BLE001
                log.error("Error notificando el nombre del terminal: %s", e)

    # ---- Aviso de cortes a BakeliteApi ----
    def _avisar_incidentes(self):
        """Informa los cortes recuperados usando ENDPOINT_REGISTRAR_INCIDENTE.

        El `idIncidente` es el UUID que creó la BD local al abrir el corte y se
        reenvía idéntico en cada reintento: la API deduplica por
        (idTerminal, idIncidente). Del terminal solo viaja su id, nada más.
        """
        if not config.USAR_BD_LOCAL or not config.ENDPOINT_REGISTRAR_INCIDENTE:
            return
        for inc in self.bd_local.incidentes_por_avisar():
            payload = self._payload_incidente(inc)
            if payload is None:
                continue
            if not self._enviar_incidente(inc, payload):
                return          # sin red: el resto espera al próximo ciclo

    def _payload_incidente(self, inc):
        """Arma el cuerpo del aviso y valida lo que exige el contrato. Devuelve
        None si el corte no es reportable (y lo deja marcado como tal)."""
        deteccion, recuperacion = inc["deteccion"], inc["recuperacion"]
        duracion = inc.get("duracion_segundos")
        if duracion is None and isinstance(deteccion, datetime.datetime) \
                and isinstance(recuperacion, datetime.datetime):
            duracion = int((recuperacion - deteccion).total_seconds())

        # El contrato exige recuperación posterior en al menos 1 segundo. Un
        # corte que empieza y termina dentro del mismo segundo sería rechazado
        # con 400, así que no se envía: queda en la BD local como NO_APLICA.
        if duracion is None or duracion < 1:
            self.bd_local.descartar_incidente(
                inc["id"], "Corte inferior a 1 segundo: no reportable según el contrato")
            log.info("Corte #%s descartado: duró menos de 1 segundo.", inc["id"])
            return None

        if not inc.get("uuid"):
            self.bd_local.descartar_incidente(inc["id"], "Incidente sin idIncidente (UUID)")
            log.error("Corte #%s sin UUID: no se puede informar.", inc["id"])
            return None

        return {
            "idIncidente": inc["uuid"],
            "idTerminal": config.ID_TERMINAL,
            "servicio": inc["servicio"],
            "fechaDeteccion": _iso(deteccion),
            "fechaRecuperacion": _iso(recuperacion),
            "duracionSegundos": duracion,
            "intentosFallidos": max(1, int(inc.get("intentos_fallidos") or 1)),
            "detalle": (inc.get("ultimo_error") or None),
        }

    def _enviar_incidente(self, inc, payload):
        """Devuelve False solo si se cayó la red (para cortar el ciclo)."""
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            config.ENDPOINT_REGISTRAR_INCIDENTE, data=data, method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=config.API_TIMEOUT_SEGUNDOS) as resp:
                cuerpo = resp.read().decode("utf-8", "ignore")
                code = resp.getcode()
            id_registro, estado = self._parse_incidente(cuerpo)
            # 201 REGISTRADO y 200 DUPLICADO cuentan igual: quedó entregado.
            self.bd_local.registrar_aviso_incidente(
                inc["id"], "OK", http_status=code, respuesta=cuerpo,
                id_registro_api=id_registro, estado_api=estado)
            log.info("Corte #%s informado a BakeliteApi (HTTP %s, %s, idRegistro=%s).",
                     inc["id"], code, estado, id_registro)
            return True
        except urllib.error.HTTPError as e:
            detalle = self._leer(e)
            if e.code == 400:
                self.bd_local.registrar_aviso_incidente(
                    inc["id"], "PERMANENTE", http_status=e.code, respuesta=detalle,
                    error=f"HTTP 400: {detalle[:300]}")
                log.error("Corte #%s rechazado por la API (400): %s", inc["id"], detalle[:300])
            else:
                self.bd_local.registrar_aviso_incidente(
                    inc["id"], "REINTENTAR", http_status=e.code, respuesta=detalle,
                    error=f"HTTP {e.code}: {detalle[:300]}")
            return True
        except Exception as e:  # noqa: BLE001
            self.bd_local.registrar_aviso_incidente(inc["id"], "REINTENTAR",
                                                    error=f"Fallo de red: {e}")
            return False

    @staticmethod
    def _parse_incidente(cuerpo):
        try:
            d = json.loads(cuerpo)
            return d.get("idRegistro"), d.get("estado")
        except Exception:  # noqa: BLE001
            return None, None


def _iso(valor):
    """Fecha de la BD -> texto ISO 8601 para el payload."""
    if valor is None:
        return None
    if isinstance(valor, datetime.datetime):
        if valor.tzinfo is None:
            valor = valor.astimezone()
        return valor.isoformat(timespec="seconds")
    return str(valor)
