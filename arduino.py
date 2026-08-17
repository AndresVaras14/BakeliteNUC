# -*- coding: utf-8 -*-
"""
Controlador del Arduino (relés + luces del semáforo).

Protocolo (§6): comandos ASCII terminados en '*', enviados con .encode('ascii').
El PC no lee respuestas del Arduino.

IMPORTANTE (§16.1): todas las escrituras al Arduino pasan por un único Lock,
para que dos hilos (Lectora Entrada, Lectora Salida, temporizadores) no
intercalen tramas y las corrompan.

Si no hay pyserial o no hay puerto, funciona en MODO SIMULADO: registra en el
log el comando que se HABRÍA enviado (útil para probar la lógica sin hardware).
"""

import time
import threading
import logging

import config

try:
    import serial
except ImportError:  # pragma: no cover
    serial = None

log = logging.getLogger("arduino")


class Arduino:
    def __init__(self):
        self.puerto = None
        self.ser = None
        self._lock = threading.Lock()

    # ---- Conexión ----
    def conectar(self, puerto):
        self.cerrar()
        self.puerto = puerto
        if serial is None:
            log.warning("pyserial no disponible; Arduino en modo simulado (%s)", puerto)
            return False
        try:
            time.sleep(config.ARDUINO_SLEEP_APERTURA)   # auto-reset del Uno
            self.ser = serial.Serial(puerto, config.BAUD_ARDUINO,
                                     timeout=config.TIMEOUT_ARDUINO)
            time.sleep(config.ARDUINO_SLEEP_APERTURA)    # espera bootloader
            log.info("Arduino conectado en %s", puerto)
            return True
        except Exception as e:  # noqa: BLE001
            log.error("No se pudo abrir el Arduino en %s: %s", puerto, e)
            self.ser = None
            return False

    @property
    def conectado(self):
        return self.ser is not None and getattr(self.ser, "is_open", False)

    def _enviar(self, comando):
        with self._lock:
            if self.conectado:
                try:
                    self.ser.write(comando.encode("ascii"))
                    log.info("-> Arduino: %s", comando)
                    return True
                except Exception as e:  # noqa: BLE001
                    log.error("Error escribiendo '%s': %s", comando, e)
                    return False
            log.info("-> Arduino [SIM]: %s", comando)
            return False

    # ---- Relés (pulso único por acceso, §6.1) ----
    def rele_entrada(self):
        self._enviar(config.RELE1)   # R2*

    def rele_salida(self):
        self._enviar(config.RELE2)   # R1*

    def pulso_rele(self, sentido):
        if sentido == "E":
            self.rele_entrada()
        else:
            self.rele_salida()

    def pulso(self, comando):
        """Dispara un relé por comando explícito (usado con los ajustes de inversión)."""
        self._enviar(comando)

    # ---- Luces ----
    def luz_azul(self):
        self._enviar(config.LUZ_AZUL)

    def luz_verde(self):
        self._enviar(config.LUZ_VERDE)

    def luz_roja(self):
        self._enviar(config.LUZ_ROJA)

    def luz_amarilla(self):
        self._enviar(config.LUZ_AMARILLA)

    def apagar_luz(self):
        self._enviar(config.LUZ_OFF)

    def apagar_luz_despues(self, segundos):
        t = threading.Timer(segundos, self.apagar_luz)
        t.daemon = True
        t.start()

    def blink_listo(self):
        self._enviar(config.BLINK_LISTO)

    # ---- Cierre ----
    def cerrar(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:  # noqa: BLE001
                pass
        self.ser = None
