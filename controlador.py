# -*- coding: utf-8 -*-
"""
Orquestador: une lectora -> validación -> luz + relé -> pantalla.

Flujo de un acceso (§7):
  1. inicio_lectura(sentido): al primer byte enciende AZUL (relé + pantalla) y
     lo mantiene HASTA que llega la respuesta.
  2. procesar_trama(...): extrae el RUT, valida, y según el código:
       cód 1 -> pulso de relé (antes del verde) + VERDE + "AUTORIZADO"
       cód 0/2/3 -> ROJO + mensaje
       cód 4 -> AMARILLO (+ LOFF* diferido)
  3. Tras unos segundos vuelve al estado "esperando" (apaga luces).

Un Lock serializa el procesamiento: la pantalla es una sola y el semáforo es
uno solo, así los resultados no se solapan.
"""

import threading
import logging

import config
from validador import Resultado, MENSAJES

log = logging.getLogger("controlador")


class Controlador:
    def __init__(self, arduino, validador, ui=None, ajustes=None):
        self.arduino = arduino
        self.validador = validador
        self.ui = ui
        self.ajustes = ajustes
        self._lock = threading.Lock()
        self._timer_idle = None

    # ---- Estado en caliente ----
    def _cancelar_idle(self):
        if self._timer_idle is not None:
            self._timer_idle.cancel()
            self._timer_idle = None

    def _programar_idle(self):
        self._cancelar_idle()
        self._timer_idle = threading.Timer(
            config.SEGUNDOS_MOSTRAR_RESULTADO, self._idle)
        self._timer_idle.daemon = True
        self._timer_idle.start()

    def _idle(self):
        self.arduino.apagar_luz()
        if self.ui:
            self.ui.mostrar_esperando()

    # ---- Llamado por la lectora ----
    def inicio_lectura(self, sentido="E"):
        """Primer byte: azul se mantiene (relé + pantalla) hasta la respuesta."""
        self._cancelar_idle()
        self.arduino.luz_azul()             # L1B*
        if self.ui:
            self.ui.mostrar_consultando(sentido)

    def _sentido_efectivo(self, sentido):
        return self.ajustes.sentido_efectivo(sentido) if self.ajustes else sentido

    def procesar_trama(self, trama, numero, sentido, simular_sin_conexion=False):
        with self._lock:
            s_ef = self._sentido_efectivo(sentido)
            resultado = self.validador.validar_trama(
                trama, s_ef, simular_sin_conexion=simular_sin_conexion)
            self._aplicar(resultado)

    def reportar_error(self, sentido):
        """Lectura incompleta/timeout de la lectora -> código 3."""
        with self._lock:
            self._aplicar(Resultado(3, MENSAJES[3], False, self._sentido_efectivo(sentido)))

    def probar_rele(self, sentido_logico):
        """Dispara el relé correspondiente a un sentido lógico (para calibrar)."""
        cmd = self.ajustes.comando_rele(sentido_logico) if self.ajustes \
            else (config.RELE1 if sentido_logico == "E" else config.RELE2)
        log.info("Prueba de relé %s -> %s", sentido_logico, cmd)
        self.arduino.pulso(cmd)

    def probar_luz(self, color):
        """Enciende una luz del semáforo (para probar el Arduino)."""
        mapa = {
            "azul": self.arduino.luz_azul,
            "verde": self.arduino.luz_verde,
            "rojo": self.arduino.luz_roja,
            "amarillo": self.arduino.luz_amarilla,
            "off": self.arduino.apagar_luz,
        }
        fn = mapa.get(color)
        if fn:
            log.info("Prueba de luz: %s", color)
            fn()

    # ---- Aplicar resultado a hardware + UI ----
    def _aplicar(self, resultado):
        codigo = resultado.codigo

        if codigo == 1:                                  # HABILITADO
            if self.ajustes:
                self.arduino.pulso(self.ajustes.comando_rele(resultado.sentido))
            else:
                self.arduino.pulso_rele(resultado.sentido)   # relé ANTES del verde
            self.arduino.luz_verde()
        elif codigo == 4:                                # SIN CONEXIÓN
            self.arduino.luz_amarilla()
            self.arduino.apagar_luz_despues(config.APAGAR_LUZ_AMARILLA_DESPUES)
        else:                                            # 0, 2, 3
            self.arduino.luz_roja()

        log.info("Acceso %s | cód %d | %s | RUT %s",
                 resultado.sentido, codigo, resultado.mensaje, resultado.rut_norm)

        if self.ui:
            self.ui.mostrar_resultado(resultado)
        self._programar_idle()

    # ---- Simulación (sin hardware) ----
    def simular(self, valor, sentido="E", sin_conexion=False, error_lectura=False):
        """Inyecta un acceso como si lo hubiera leído una lectora.
        Se ejecuta en un hilo aparte para no bloquear la UI durante la consulta."""
        def worker():
            self.inicio_lectura(sentido)
            if error_lectura:
                trama = "BASURA-SIN-FORMATO-XYZ"
            else:
                trama = f"?RUN={valor}&SEC=1"
            self.procesar_trama(trama, 0, sentido, simular_sin_conexion=sin_conexion)

        threading.Thread(target=worker, daemon=True).start()
