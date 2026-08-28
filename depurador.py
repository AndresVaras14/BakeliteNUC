# -*- coding: utf-8 -*-
"""
Registro de depuración: qué se hace y qué se recibe, paso a paso.

El modo debugger parte la pantalla en dos y muestra este registro en vivo al
lado de la aplicación. Sirve para entender qué pasó cuando algo falla en el
equipo instalado, sin tener que reproducirlo mirando la consola.

Dos fuentes alimentan el registro:

  - las acciones y respuestas que se anotan a mano (`accion`, `respuesta`),
    que son las que cuentan la historia legible: "se pasó una cédula",
    "la API respondió HABILITADO";
  - todo lo que la aplicación ya escribe con `logging`, que entra por
    `ManejadorDepuracion` y aporta el detalle fino.

El archivo vive en logs/ y se conserva entre reinicios: al entrar en modo
debugger se carga lo que ya había, así se puede revisar lo que pasó antes.
"""

import os
import time
import logging
import threading
import datetime

import config

log = logging.getLogger("depurador")

# Marcas de flujo. Se eligieron de un solo carácter para que las columnas
# queden alineadas y el registro se lea de un vistazo.
ACCION = "→"      # algo que hace el operador o la aplicación
RESPUESTA = "←"   # algo que llega de afuera (lectora, API, Arduino)
INFO = "·"        # contexto interno


class Depurador:
    """Acumula el registro, lo persiste y avisa a quien lo esté mirando."""

    def __init__(self, ruta=None, max_bytes=None):
        self.ruta = ruta or config.ARCHIVO_LOG_DEBUG
        self.max_bytes = max_bytes or config.DEBUG_MAX_BYTES
        self._lock = threading.Lock()
        self._suscriptores = []
        self.activo = False
        os.makedirs(os.path.dirname(self.ruta), exist_ok=True)

    # ---- Suscripción (la usa la pantalla para verlo en vivo) ----
    def suscribir(self, fn):
        with self._lock:
            if fn not in self._suscriptores:
                self._suscriptores.append(fn)

    def desuscribir(self, fn):
        with self._lock:
            if fn in self._suscriptores:
                self._suscriptores.remove(fn)

    # ---- Registro ----
    def accion(self, texto, origen=None):
        self.registrar(ACCION, texto, origen)

    def respuesta(self, texto, origen=None):
        self.registrar(RESPUESTA, texto, origen)

    def info(self, texto, origen=None):
        self.registrar(INFO, texto, origen)

    def registrar(self, flujo, texto, origen=None, registrar_en_logging=True):
        """Arma la línea, la guarda y la reparte. No revienta nunca: un fallo
        acá no puede tumbar el acceso de una persona."""
        linea = self._formatear(flujo, texto, origen)
        try:
            self._guardar(linea)
        except Exception as e:  # noqa: BLE001
            log.debug("No se pudo guardar la línea de depuración: %s", e)
        with self._lock:
            suscriptores = list(self._suscriptores)
        for fn in suscriptores:
            try:
                fn(linea)
            except Exception as e:  # noqa: BLE001
                log.debug("Suscriptor de depuración falló: %s", e)
        # Las acciones manuales del flujo también entran a la bitácora SQLite.
        # Cuando esta llamada viene desde ManejadorDepuracion se desactiva para
        # no duplicar cada LogRecord ni crear un bucle.
        if registrar_en_logging:
            logging.getLogger("flujo").info(
                texto,
                extra={"flujo": flujo, "origen": origen},
            )
        return linea

    @staticmethod
    def _formatear(flujo, texto, origen):
        hora = datetime.datetime.now().strftime("%H:%M:%S")
        etiqueta = f"[{origen}] " if origen else ""
        return f"{hora}  {flujo}  {etiqueta}{texto}"

    def _guardar(self, linea):
        with self._lock:
            self._rotar_si_hace_falta()
            with open(self.ruta, "a", encoding="utf-8") as f:
                f.write(linea + "\n")

    def _rotar_si_hace_falta(self):
        """Un archivo que crece sin tope termina siendo imposible de abrir.
        Al pasarse, se conserva una copia .1 y se empieza de nuevo."""
        try:
            if os.path.getsize(self.ruta) < self.max_bytes:
                return
        except OSError:
            return
        anterior = self.ruta + ".1"
        try:
            if os.path.exists(anterior):
                os.remove(anterior)
            os.replace(self.ruta, anterior)
        except OSError as e:
            log.debug("No se pudo rotar el registro de depuración: %s", e)

    # ---- Lectura ----
    def historial(self, max_lineas=None):
        """Lo ya registrado, para mostrarlo al entrar en modo debugger."""
        max_lineas = max_lineas or config.DEBUG_LINEAS_HISTORIAL
        try:
            with open(self.ruta, "r", encoding="utf-8", errors="ignore") as f:
                lineas = f.read().splitlines()
        except FileNotFoundError:
            return []
        except Exception as e:  # noqa: BLE001
            log.error("No se pudo leer %s: %s", self.ruta, e)
            return []
        return lineas[-max_lineas:]

    def limpiar(self):
        with self._lock:
            try:
                open(self.ruta, "w", encoding="utf-8").close()
                return True
            except Exception as e:  # noqa: BLE001
                log.error("No se pudo limpiar %s: %s", self.ruta, e)
                return False


class ManejadorDepuracion(logging.Handler):
    """Vuelca al registro de depuración todo lo que la app escribe con logging.

    Se instala en el logger raíz, así no hay que tocar cada módulo para que su
    detalle aparezca en la pantalla del debugger.
    """

    def __init__(self, depurador, nivel=logging.DEBUG):
        super().__init__(nivel)
        self.depurador = depurador

    def emit(self, record):
        # El propio depurador escribe logs cuando algo le falla; reenviarlos
        # aquí sería un bucle.
        if record.name in ("depurador", "flujo", "bitacora"):
            return
        try:
            flujo = RESPUESTA if record.levelno >= logging.WARNING else INFO
            texto = record.getMessage()
            if record.levelno >= logging.ERROR:
                texto = f"ERROR: {texto}"
            self.depurador.registrar(
                flujo, texto, origen=record.name, registrar_en_logging=False)
        except Exception:  # noqa: BLE001
            self.handleError(record)


# Instancia única: la comparten la app y la pantalla.
depurador = Depurador()
