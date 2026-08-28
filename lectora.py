# -*- coding: utf-8 -*-
"""
Hilo de lectura de una lectora de cédula (§8.3).

Cada lectora corre en su propio hilo:
  - Abre el puerto a 9600, no bloqueante (timeout=0).
  - Al primer byte -> callback on_inicio (enciende azul + "VALIDANDO ACCESO").
  - Acumula hasta encontrar '?RUN=' o 'CHL' -> callback on_trama (valida).
  - Overflow (>256 chars sin trama) -> descarta.
  - Lectura incompleta (2 s sin trama) -> descarta + on_error (código 3).
  - Watchdog: 5 descartes seguidos -> reabre el puerto.
  - Watchdog: reabre por mantenimiento cada 3600 s.

Si no hay pyserial o no hay puerto, el hilo simplemente no hace nada
(se usa el modo simulación por teclado).
"""

import time
import threading
import logging

import config

try:
    import serial
except ImportError:  # pragma: no cover
    serial = None

log = logging.getLogger("lectora")


class LectoraThread(threading.Thread):
    def __init__(self, numero, sentido, puerto, on_inicio, on_trama, on_error):
        super().__init__(daemon=True, name=f"Lectora{numero}")
        self.numero = numero
        self.sentido = sentido
        self.puerto = puerto
        self.on_inicio = on_inicio          # cuando llega el primer byte
        self.on_trama = on_trama            # (trama, numero, sentido)
        self.on_error = on_error            # (numero, sentido) lectura inválida
        self._detener_evento = threading.Event()
        self.ser = None

    def detener(self):
        self._detener_evento.set()

    def _abrir(self):
        if serial is None or not self.puerto:
            return False
        try:
            self.ser = serial.Serial(self.puerto, config.BAUD_LECTORA,
                                     timeout=config.TIMEOUT_LECTORA)
            try:
                self.ser.reset_input_buffer()
            except Exception as e:  # noqa: BLE001
                log.debug("Lectora %d no pudo limpiar el buffer: %s", self.numero, e)
            log.info("Lectora %d (%s) abierta en %s",
                     self.numero, self.sentido, self.puerto)
            return True
        except Exception as e:  # noqa: BLE001
            log.error("No se pudo abrir la lectora %d en %s: %s",
                      self.numero, self.puerto, e)
            self.ser = None
            return False

    def run(self):
        while not self._detener_evento.is_set():
            if not self._abrir():
                time.sleep(5)   # reintento sin puerto (§10)
                continue

            data = ""
            leyendo = False
            errores = 0
            t_ultimo = time.time()
            t_abierta = time.time()

            while not self._detener_evento.is_set():
                try:
                    n = self.ser.in_waiting
                except Exception as e:  # noqa: BLE001
                    log.warning("Lectora %d: puerto perdido (%s)", self.numero, e)
                    break

                if n > 0:
                    chunk = self.ser.read(min(n, 1024)).decode("latin-1", "ignore")
                    if not leyendo:
                        leyendo = True
                        self.on_inicio()
                    data += chunk
                    t_ultimo = time.time()

                    if "?RUN=" in data or "CHL" in data:
                        self.on_trama(data, self.numero, self.sentido)
                        data, leyendo, errores = "", False, 0
                    elif len(data) > 256:                     # basura
                        data, leyendo = "", False
                        errores += 1
                else:
                    if leyendo and (time.time() - t_ultimo) > \
                            config.LECTURA_INCOMPLETA_TIMEOUT_SEGUNDOS:
                        data, leyendo = "", False
                        errores += 1
                        self.on_error(self.numero, self.sentido)
                    time.sleep(config.LECTURA_POLL_SEGUNDOS)

                if errores >= config.LECTORA_MAX_ERRORES_CONSECUTIVOS:
                    log.warning("Lectora %d: %d errores seguidos, reabriendo",
                                self.numero, errores)
                    break
                if (time.time() - t_abierta) > config.LECTORA_WATCHDOG_REABRIR_SEGUNDOS:
                    log.info("Lectora %d: reapertura por mantenimiento", self.numero)
                    break

            try:
                if self.ser:
                    self.ser.close()
            except Exception as e:  # noqa: BLE001
                log.debug("Lectora %d no pudo cerrar el puerto: %s", self.numero, e)
            self.ser = None
