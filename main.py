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
import platform
import threading
import time
from logging.handlers import RotatingFileHandler

import config
from bitacora import ManejadorBitacoraSQLite
from depurador import depurador, ManejadorDepuracion
import deteccion_puertos
from ajustes import Ajustes
from presencia import Heartbeat
from dispositivos import SincronizadorDispositivos
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
    # DEBUG queda disponible para la bitácora SQLite y el panel de diagnóstico;
    # consola y archivos aplican sus propios niveles para no llenarse de ruido.
    root.setLevel(logging.DEBUG)

    # Todo lo que la app registra alimenta también el modo debugger, así su
    # panel muestra el detalle fino sin tener que instrumentar cada módulo.
    root.addHandler(ManejadorDepuracion(depurador))

    consola = logging.StreamHandler()
    consola.setFormatter(fmt)
    consola.setLevel(logging.INFO)
    root.addHandler(consola)

    app = RotatingFileHandler(config.ARCHIVO_LOG, maxBytes=1_000_000, backupCount=3,
                              encoding="utf-8")
    app.setFormatter(fmt)
    app.setLevel(logging.INFO)
    root.addHandler(app)

    err = RotatingFileHandler(config.ARCHIVO_LOG_ERRORES, maxBytes=1_000_000, backupCount=5,
                              encoding="utf-8")
    err.setFormatter(fmt)
    err.setLevel(logging.ERROR)
    root.addHandler(err)

    # Registro estructurado e independiente de SQL Server/Internet. Si el disco
    # local no permite abrir SQLite, los logs de texto siguen operativos y la
    # aplicación puede iniciar para mostrar el problema.
    try:
        root.addHandler(ManejadorBitacoraSQLite())
    except Exception:  # noqa: BLE001
        root.exception("No se pudo activar la bitácora SQLite local.")


def _instalar_hooks_excepciones():
    """Registra fallos que Python normalmente dejaría solo en stderr."""
    def excepcion_hilo(args):
        if args.exc_type is SystemExit:
            return
        logging.getLogger("errores").critical(
            "Excepción no controlada en el hilo %s",
            args.thread.name if args.thread else "desconocido",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    threading.excepthook = excepcion_hilo

    def excepcion_no_observable(args):
        logging.getLogger("errores").critical(
            "Excepción no observable%s en %r",
            f" ({args.err_msg})" if args.err_msg else "",
            args.object,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.unraisablehook = excepcion_no_observable
    logging.captureWarnings(True)


_configurar_logging()
_instalar_hooks_excepciones()
log = logging.getLogger("main")


def main():
    depurador.info("══════════ NUEVA EJECUCIÓN ══════════", origen="sistema")
    log.info("Entorno de ejecución: %s · Python %s · %s",
             platform.system(), platform.python_version(), sys.executable)
    validador = Validador(config.ARCHIVO_PERSONAS)
    arduino = Arduino()
    store = RegistroStore()

    # BD local (SQL Server / BakeliteTorniquete). Si no está disponible la app
    # arranca igual: las marcas quedan en SQLite y se guardan al reconectar.
    bd_local = BDLocal()
    # La configuración de lectoras y relés vive en la BD; el JSON es respaldo.
    ajustes = Ajustes(bd_local=bd_local)
    if bd_local.disponible:
        term = bd_local.terminal() or {}
        ver = bd_local.version_activa() or {}
        log.info("BD local OK — terminal %s: %s · versión %s",
                 term.get("id"), term.get("nombre"), ver.get("numero"))
    else:
        log.error("BD local no disponible (%s). Las marcas quedan en cola.",
                  bd_local.ultimo_error)

    sincronizador = Sincronizador(store, bd_local, on_estado=None)
    # Presencia del proceso ante Bakelite. Hilo aparte del sincronizador: su
    # cadencia es fija y no puede quedar detrás de una subida lenta, o la web
    # mostraría una caída que no ocurrió.
    heartbeat = Heartbeat(bd_local)
    # Lectoras y relés: manda su configuración y su estado, y adopta lo que
    # cambien desde la web. Hilo aparte por lo mismo que el heartbeat: su
    # cadencia no puede quedar detrás de una subida lenta de marcas.
    dispositivos = SincronizadorDispositivos(bd_local)

    controlador = Controlador(arduino, validador, ui=None, ajustes=ajustes,
                              store=store, sincronizador=sincronizador,
                              bd_local=bd_local)

    puertos = {"arduino": None, "lectora1": None, "lectora2": None}
    hilos = {1: None, 2: None}
    pares = [(1, config.SENTIDO_LECTORA1, "lectora1"),
             (2, config.SENTIDO_LECTORA2, "lectora2")]

    def _crear_lectora(n, sentido, puerto):
        # El sentido nominal ya no decide nada: el controlador lo resuelve por
        # el NÚMERO de lectora contra la configuración de la BD (dbo.Lectoras).
        return LectoraThread(
            n, sentido, puerto,
            on_inicio=lambda num=n: controlador.inicio_lectura(num),
            on_trama=controlador.procesar_trama,
            on_error=lambda num, s: controlador.reportar_error(num),
        )

    def estado():
        # Los puertos viajan junto al estado: la pantalla de Ajustes muestra el
        # puerto REAL de este momento, no el último que quedó en la BD.
        return {
            "arduino": arduino.conectado,
            "lectora1": bool(puertos["lectora1"]),
            "lectora2": bool(puertos["lectora2"]),
            "puertos": dict(puertos),
        }

    def detectar_y_conectar():
        """Detecta el hardware y lo engancha o suelta según lo que haya ahora.
        Se llama al arrancar, desde el botón "Volver a detectar" y de forma
        periódica, así conectar o desconectar un aparato se nota solo."""
        # Las anclas guardadas hacen que cada lectora conserve su número: sin
        # ellas la asignación era por orden de aparición, y al desenchufar una
        # la sobreviviente ocupaba su lugar. El sistema terminaba informando
        # desconectada a la lectora que seguía funcionando.
        anclas = {}
        if bd_local.disponible:
            for f in (bd_local.lectoras() or []):
                if f.get("ancla"):
                    anclas[f"lectora{f['numero']}"] = f["ancla"]
        p = deteccion_puertos.detectar(anclas)
        anclas_vistas = p.pop("anclas", {})

        # --- Arduino ---
        if not p["arduino"] and arduino.conectado:
            log.warning("Arduino desconectado (%s).", puertos.get("arduino"))
            arduino.cerrar()
            if bd_local.disponible:
                bd_local.estado_arduino(False)
                dispositivos.notificar()
        elif p["arduino"] and (not arduino.conectado or p["arduino"] != puertos.get("arduino")):
            if arduino.conectar(p["arduino"]):
                log.info("Arduino conectado en %s.", p["arduino"])
                arduino.blink_listo()
                arduino.apagar_luz()
                if bd_local.disponible:
                    bd_local.estado_arduino(True, p["arduino"])
                    dispositivos.notificar()

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
                    if bd_local.disponible:
                        bd_local.estado_lectora(n, conectada=False,
                                                error="Se desconectó del puerto")
                        dispositivos.notificar()
                continue
            if not vivo or p[key] != puertos.get(key):
                if vivo:
                    th.detener()        # cambió de puerto: se reengancha al nuevo
                log.info("Lectora %d conectada en %s.", n, p[key])
                if bd_local.disponible:
                    bd_local.estado_lectora(n, conectada=True, puerto=p[key],
                                            ancla=anclas_vistas.get(key))
                    dispositivos.notificar()
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
    # Si Bakelite tiene un nombre más nuevo, el sincronizador lo adopta y la
    # pantalla lo refleja sola, sin reiniciar la app.
    sincronizador.on_nombre = ui.set_nombre_terminal
    # Un terminal dado de baja detiene el heartbeat: el contrato exige avisarle
    # al operador, porque desde la web ese equipo deja de existir.
    heartbeat.on_critico = ui.mostrar_critico
    heartbeat.on_resuelto = ui.limpiar_critico
    dispositivos.on_critico = ui.mostrar_critico
    dispositivos.on_resuelto = ui.limpiar_critico
    # Si la configuración cambió desde la web, la pantalla lo refleja sola.
    dispositivos.on_cambio = ui.recargar_dispositivos
    controlador.dispositivos = dispositivos

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

    # Verificación del terminal contra Bakelite: comprueba que el id exista y
    # esté activo, y de paso sincroniza el nombre en ambos sentidos (gana el
    # cambio más reciente). Ver CONTRATO_SINCRONIZACION_NOMBRE_TERMINAL.md.
    threading.Thread(target=sincronizador.verificar_terminal, daemon=True,
                     name="VerificarTerminal").start()
    sincronizador.start()
    heartbeat.start()
    dispositivos.start()

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

    cerrando = threading.Event()

    def cerrar():
        if cerrando.is_set():
            return
        cerrando.set()
        log.info("Cierre ordenado de la aplicación iniciado.")
        parar_vigilancia.set()
        try:
            sincronizador.detener()
            heartbeat.detener()
            dispositivos.detener()
        except Exception:  # noqa: BLE001
            log.exception("Error deteniendo servicios de segundo plano.")
        for th in hilos.values():
            if th is not None:
                th.detener()
        # Esperar primero a quienes pueden seguir usando la cola o la BD. El
        # límite evita que una petición de red lenta bloquee indefinidamente
        # el cierre de la ventana.
        servicios = [sincronizador, heartbeat, dispositivos, hilo_vigilancia]
        servicios.extend(th for th in hilos.values() if th is not None)
        limite_cierre = time.monotonic() + 5
        for servicio in servicios:
            if servicio.is_alive():
                servicio.join(timeout=max(0, limite_cierre - time.monotonic()))
            if servicio.is_alive():
                log.warning(
                    "El hilo %s no alcanzó a detenerse antes del cierre.",
                    servicio.name,
                )
        arduino.apagar_luz()
        arduino.cerrar()
        try:
            bd_local.cerrar()
        except Exception:  # noqa: BLE001
            log.exception("No se pudo cerrar SQL Server local correctamente.")
        # El sincronizador es el único servicio que utiliza RegistroStore. Si
        # aún está terminando una llamada de red, el proceso/SQLite cerrarán su
        # conexión al salir; cerrarla aquí produciría una carrera y una marca
        # de error artificial.
        if not sincronizador.is_alive():
            try:
                store.cerrar()
            except Exception:  # noqa: BLE001
                log.exception("No se pudo cerrar la cola SQLite correctamente.")
        else:
            log.warning(
                "La cola SQLite queda abierta hasta finalizar el proceso porque "
                "el sincronizador todavía está cerrando."
            )
        log.info("Cierre ordenado de la aplicación completado.")

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
