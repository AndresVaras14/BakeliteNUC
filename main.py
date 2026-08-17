# -*- coding: utf-8 -*-
"""
Punto de entrada del sistema de control de acceso BAKELITE.

Secuencia de arranque (§11):
  1. Detectar y asignar puertos (Arduino + 2 lectoras).
  2. Abrir el Arduino y lanzar los hilos de lectura.
  3. Enviar el parpadeo "sistema listo".
  4. Levantar la interfaz (con la pantalla de estado de conexión).

Si no se detecta hardware, arranca en MODO SIMULACIÓN (teclas 1–6, etc.).
La detección/conexión está en `detectar_y_conectar`, reutilizable por el botón
"Volver a detectar" de la pantalla de estado.
"""

import atexit
import logging

import config
import deteccion_puertos
from ajustes import Ajustes
from validador import Validador
from arduino import Arduino
from lectora import LectoraThread
from controlador import Controlador
from interfaz import Interfaz

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")


def main():
    ajustes = Ajustes()
    validador = Validador(config.ARCHIVO_PERSONAS)
    arduino = Arduino()
    controlador = Controlador(arduino, validador, ui=None, ajustes=ajustes)

    puertos = {"arduino": None, "lectora1": None, "lectora2": None}
    hilos = {1: None, 2: None}
    pares = [(1, config.SENTIDO_LECTORA1, "lectora1"),
             (2, config.SENTIDO_LECTORA2, "lectora2")]

    def _crear_lectora(n, sentido, puerto):
        return LectoraThread(
            n, sentido, puerto,
            on_inicio=lambda s=sentido: controlador.inicio_lectura(s),
            on_trama=controlador.procesar_trama,
            on_error=lambda num, s: controlador.reportar_error(s),
        )

    def estado():
        return {
            "arduino": arduino.conectado,
            "lectora1": bool(puertos["lectora1"]),
            "lectora2": bool(puertos["lectora2"]),
        }

    def detectar_y_conectar():
        """Detecta puertos, conecta el Arduino y arranca las lectoras que falten.
        Devuelve el estado actual del hardware. Reutilizable en caliente."""
        p = deteccion_puertos.detectar()
        puertos.update(p)

        if p["arduino"] and not arduino.conectado:
            if arduino.conectar(p["arduino"]):
                arduino.blink_listo()
                arduino.apagar_luz()

        for n, sentido, key in pares:
            th = hilos[n]
            if p[key] and (th is None or not th.is_alive()):
                nuevo = _crear_lectora(n, sentido, p[key])
                nuevo.start()
                hilos[n] = nuevo
        return estado()

    # --- Arranque ---
    st = detectar_y_conectar()
    sim = not any(puertos.values())

    ui = Interfaz(controlador=controlador, sim=sim,
                  estado_hw=st, redetectar=detectar_y_conectar)
    controlador.ui = ui

    if sim:
        log.warning("Sin hardware detectado — MODO SIMULACIÓN (teclas 1–6).")
        ui.set_conexion("Modo simulación", ok=False)
    else:
        faltan = [k for k, v in st.items() if not v]
        if faltan:
            log.warning("Hardware incompleto, falta: %s", ", ".join(faltan))
            ui.set_conexion("Hardware incompleto", ok=False)
        else:
            log.info("Hardware completo: Arduino + 2 lectoras")
            ui.set_conexion("Sistema en línea", ok=True)

    def cerrar():
        for th in hilos.values():
            if th is not None:
                th.detener()
        arduino.apagar_luz()
        arduino.cerrar()

    atexit.register(cerrar)
    ui.root.protocol("WM_DELETE_WINDOW", lambda: (cerrar(), ui.root.destroy()))

    ui.run()


if __name__ == "__main__":
    main()
