# -*- coding: utf-8 -*-
"""Fachada del sincronizador durable del NUC.

La entrega de eventos, la salud de API, el nombre del terminal y los incidentes
viven en ``sincronizacion_componentes``. Se conserva la clase pública
``Sincronizador`` para no alterar ningún llamador.
"""

import datetime
import logging
import threading
import time

import config
from sincronizacion_componentes.estado_api import EstadoApiMixin
from sincronizacion_componentes.eventos import EventosMixin
from sincronizacion_componentes.incidentes import IncidentesApiMixin
from sincronizacion_componentes.nombre_api import NombreApiMixin
from sincronizacion_componentes.nombre_terminal import NombreTerminalMixin

log = logging.getLogger("sincronizador")


class Sincronizador(
    EventosMixin,
    EstadoApiMixin,
    NombreTerminalMixin,
    NombreApiMixin,
    IncidentesApiMixin,
    threading.Thread,
):
    """Coordina reintentos sin concentrar los detalles de cada integración."""

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

