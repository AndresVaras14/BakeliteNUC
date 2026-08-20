# -*- coding: utf-8 -*-
"""
Punto de entrada del sistema de control de acceso BAKELITE.

Secuencia de arranque (§11):
  1. Detectar y asignar puertos (Arduino + 2 lectoras).
  2. Abrir el Arduino y lanzar los hilos de lectura.
  3. Enviar el parpadeo "sistema listo".
  4. Levantar la interfaz (estado de conexión, luz en vivo, registros).
  5. Arrancar el sincronizador (BD local SQL Server + API).

Robustez: registra todo a logs/, captura errores críticos y —cuando se ejecuta
bajo supervisor.py— se relanza solo si se cae. Si no hay hardware, MODO SIMULACIÓN.
"""

import os
import sys
import atexit
import logging
import threading
from logging.handlers import RotatingFileHandler

import config
import deteccion_puertos
from ajustes import Ajustes
from validador import Validador
from arduino import Arduino
from lectora import LectoraThread
from controlador import Controlador
from interfaz import Interfaz
from registros import RegistroStore
from basedatos import BDLocal
from sincronizador import Sincronizador


def _configurar_logging():
    os.makedirs(config.DIR_LOGS, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    consola = logging.StreamHandler()
    consola.setFormatter(fmt)
    root.addHandler(consola)

    app = RotatingFileHandler(config.ARCHIVO_LOG, maxBytes=1_000_000, backupCount=3,
                              encoding="utf-8")
    app.setFormatter(fmt)
    root.addHandler(app)

    err = RotatingFileHandler(config.ARCHIVO_LOG_ERRORES, maxBytes=1_000_000, backupCount=5,
                              encoding="utf-8")
    err.setFormatter(fmt)
    err.setLevel(logging.ERROR)
    root.addHandler(err)


_configurar_logging()
log = logging.getLogger("main")


def main():
    ajustes = Ajustes()
    validador = Validador(config.ARCHIVO_PERSONAS)
    arduino = Arduino()
    store = RegistroStore()

    # BD local (SQL Server / BakeliteTorniquete). Si no está disponible la app
    # arranca igual: las marcas quedan en la cola JSON y se guardan al reconectar.
    bd_local = BDLocal()
    if bd_local.disponible:
        term = bd_local.terminal() or {}
        ver = bd_local.version_activa() or {}
        log.info("BD local OK — terminal %s: %s · versión %s",
                 term.get("id"), term.get("nombre"), ver.get("numero"))
    else:
        log.error("BD local no disponible (%s). Las marcas quedan en cola.",
                  bd_local.ultimo_error)

    sincronizador = Sincronizador(store, bd_local, on_estado=None)

    controlador = Controlador(arduino, validador, ui=None, ajustes=ajustes,
                              store=store, sincronizador=sincronizador,
                              bd_local=bd_local)

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
        """Detecta el hardware y lo engancha o suelta según lo que haya ahora.
        Se llama al arrancar, desde el botón "Volver a detectar" y de forma
        periódica, así conectar o desconectar un aparato se nota solo."""
        p = deteccion_puertos.detectar()

        # --- Arduino ---
        if not p["arduino"] and arduino.conectado:
            log.warning("Arduino desconectado (%s).", puertos.get("arduino"))
            arduino.cerrar()
        elif p["arduino"] and (not arduino.conectado or p["arduino"] != puertos.get("arduino")):
            if arduino.conectar(p["arduino"]):
                log.info("Arduino conectado en %s.", p["arduino"])
                arduino.blink_listo()
                arduino.apagar_luz()

        # --- Lectoras ---
        for n, sentido, key in pares:
            th = hilos[n]
            vivo = th is not None and th.is_alive()
            if not p[key]:
                # Se desenchufó: se corta el hilo para no dejarlo reintentando
                # sobre un puerto que ya no existe.
                if vivo:
                    log.warning("Lectora %d desconectada (%s).", n, puertos.get(key))
                    th.detener()
                    hilos[n] = None
                continue
            if not vivo or p[key] != puertos.get(key):
                if vivo:
                    th.detener()        # cambió de puerto: se reengancha al nuevo
                log.info("Lectora %d conectada en %s.", n, p[key])
                nuevo = _crear_lectora(n, sentido, p[key])
                nuevo.start()
                hilos[n] = nuevo

        puertos.update(p)
        return estado()

    # --- Arranque ---
    st = detectar_y_conectar()
    sim = not any(puertos.values())

    ui = Interfaz(controlador=controlador, sim=sim,
                  estado_hw=st, redetectar=detectar_y_conectar)
    controlador.ui = ui

    # Estado "en línea" -> footer con luz + última conexión, por servicio.
    sincronizador.on_estado = lambda en, ult: ui.set_en_linea(en, ult, servicio="bakelite")

    # Los indicadores parten en "verificando…", con la última conexión conocida
    # de la BD. Nadie declara una caída hasta comprobarla de verdad: el
    # sincronizador hace ping apenas arranca y la API externa se prueba aquí.
    if bd_local.disponible:
        for servicio, etiqueta in (("BAKELITE", "bakelite"), ("EXTERNA", "externa")):
            est = bd_local.estado_servicio(servicio)
            ui.set_en_linea(None, (est or {}).get("ultima_conexion"), servicio=etiqueta)

        # Historial: las últimas marcas guardadas, incluidas las no enviadas.
        ultimas = bd_local.ultimas_marcas(5)
        if ultimas:
            ui.cargar_historial(ultimas)
            log.info("Historial precargado con %d marcas de la BD local.", len(ultimas))

    controlador.comprobar_api_externa()

    # Verificación del terminal contra Bakelite: solo comprueba que el id exista
    # y esté activo. El nombre se gobierna desde esta app y su BD local.
    threading.Thread(target=sincronizador.verificar_terminal, daemon=True,
                     name="VerificarTerminal").start()
    sincronizador.start()

    # Vigilancia de puertos: revisa cada pocos segundos si algo se conectó o se
    # desconectó, y actualiza el indicador de la pantalla sin intervención.
    def vigilar_puertos():
        anterior = dict(st)
        while not parar_vigilancia.is_set():
            parar_vigilancia.wait(config.SCAN_PUERTOS_INTERVALO_SEGUNDOS)
            if parar_vigilancia.is_set():
                break
            try:
                actual = detectar_y_conectar()
            except Exception as e:  # noqa: BLE001
                log.error("Error revisando los puertos: %s", e)
                continue
            if actual != anterior:
                log.info("Cambio de hardware: %s", actual)
                ui.set_estado_hw(actual)
                anterior = dict(actual)

    parar_vigilancia = threading.Event()
    hilo_vigilancia = threading.Thread(target=vigilar_puertos, daemon=True,
                                       name="VigilanciaPuertos")
    hilo_vigilancia.start()

    if sim:
        log.warning("Sin hardware detectado — MODO SIMULACIÓN (teclas 1–6).")
    else:
        faltan = [k for k, v in st.items() if not v]
        if faltan:
            log.warning("Hardware incompleto, falta: %s", ", ".join(faltan))
        else:
            log.info("Hardware completo: Arduino + 2 lectoras")

    def cerrar():
        parar_vigilancia.set()
        try:
            sincronizador.detener()
        except Exception:  # noqa: BLE001
            pass
        for th in hilos.values():
            if th is not None:
                th.detener()
        arduino.apagar_luz()
        arduino.cerrar()
        try:
            bd_local.cerrar()
        except Exception:  # noqa: BLE001
            pass

    atexit.register(cerrar)
    ui.root.protocol("WM_DELETE_WINDOW", lambda: (cerrar(), ui.root.destroy()))

    ui.run()


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        logging.getLogger("errores").critical("Error crítico en main()", exc_info=True)
        raise
