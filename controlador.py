# -*- coding: utf-8 -*-
"""
Orquestador: une lectora -> validación -> luz + relé -> pantalla -> registro.

Flujo de un acceso (§7):
  1. inicio_lectura(sentido): al primer byte enciende AZUL (relé + pantalla) y
     lo mantiene HASTA que llega la respuesta.
  2. procesar_trama(...): extrae el RUT y, en este orden:
       a) abre la marca en la BD local: RUT, evento y fecha/hora (dbo.Marcas).
       b) le pregunta el RUT a la API externa (habilitado 1/0, nombre, motivo).
       c) guarda esa consulta y su respuesta en la BD local, sobre la misma
          marca (dbo.ConsultasApiExterna).
     Y según el código resultante:
       cód 1 -> pulso de relé (antes del verde) + VERDE + "AUTORIZADO"
       cód 0/2/3 -> ROJO + mensaje
       cód 4 -> AMARILLO (+ LOFF* diferido)
  3. Encola la marca para BakeliteApi y avisa al sincronizador, que la sube y
     deja escrito en la BD local si el envío fue exitoso o no.
  4. Tras unos segundos vuelve al estado "esperando" (apaga luces).

Si la BD local está caída, el acceso funciona igual: la marca queda en la cola
JSON y el sincronizador la reconstruye en la BD al reconectar.

Anti-doble-lectura: tras procesar una lectura de una lectora, se ignoran nuevas
lecturas de esa misma lectora durante LECTURA_COOLDOWN_SEGUNDOS (evita mandar dos
consultas al servidor por el mismo carnet).

Un Lock serializa el procesamiento: la pantalla es una sola y el semáforo es
uno solo, así los resultados no se solapan.
"""

import time
import datetime
import threading
import logging

import config
from rut import fn_enmascara_rut, formatea_rut
from validador import Resultado, MENSAJES

log = logging.getLogger("controlador")


class Controlador:
    def __init__(self, arduino, validador, ui=None, ajustes=None,
                 store=None, sincronizador=None, bd_local=None):
        self.arduino = arduino
        self.validador = validador
        self.ui = ui
        self.ajustes = ajustes
        self.store = store
        self.sincronizador = sincronizador
        self.bd_local = bd_local
        self._lock = threading.Lock()
        self._timer_idle = None
        self._cooldown = {}   # sentido nominal -> time.monotonic() del último proceso
        self._ultimo_rut = {}  # sentido nominal -> (rut, time.monotonic())

    # ---- Anti-doble-lectura ----
    def _en_cooldown(self, sentido):
        t = self._cooldown.get(sentido, 0.0)
        return (time.monotonic() - t) < config.LECTURA_COOLDOWN_SEGUNDOS

    def _marcar_lectura(self, sentido):
        self._cooldown[sentido] = time.monotonic()

    def _repetida(self, sentido, rut):
        """True si es la MISMA cédula que se acaba de leer en esta lectora.
        Cada lectura repetida reinicia la ventana: mientras la cédula siga
        apoyada sobre el lector, no se vuelve a consultar."""
        if rut == "0":
            return False
        anterior, t = self._ultimo_rut.get(sentido, (None, 0.0))
        ahora = time.monotonic()
        repetida = (anterior == rut and
                    (ahora - t) < config.LECTURA_MISMO_RUT_SEGUNDOS)
        self._ultimo_rut[sentido] = (rut, ahora)
        return repetida

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
        self._luz_ui("off")
        if self.ui:
            self.ui.mostrar_esperando()

    def _luz_ui(self, color):
        if self.ui:
            self.ui.set_luz(color)

    # ---- Llamado por la lectora ----
    def inicio_lectura(self, sentido="E"):
        """Primer byte: azul se mantiene (relé + pantalla) hasta la respuesta."""
        if self._en_cooldown(sentido):
            return
        self._cancelar_idle()
        self.arduino.luz_azul()             # L1B*
        self._luz_ui("azul")
        if self.ui:
            self.ui.mostrar_consultando(sentido)

    def _sentido_efectivo(self, sentido):
        return self.ajustes.sentido_efectivo(sentido) if self.ajustes else sentido

    def procesar_trama(self, trama, numero, sentido, simular_sin_conexion=False):
        with self._lock:
            if self._en_cooldown(sentido):
                log.info("Lectura ignorada por cooldown (%s)", sentido)
                return
            self._marcar_lectura(sentido)
            s_ef = self._sentido_efectivo(sentido)

            rut_norm = fn_enmascara_rut(trama)
            if self._repetida(sentido, rut_norm):
                log.info("Lectura repetida de la misma cédula ignorada (%s, RUT %s)",
                         sentido, rut_norm)
                return

            # a) La cédula ya pasó: se guarda la marca antes de preguntar nada.
            marca = self._abrir_marca(rut_norm, s_ef)

            # b) Consulta a la API externa (hoy la resuelve el Validador).
            t0 = time.monotonic()
            resultado = self.validador.validar(
                rut_norm, s_ef, simular_sin_conexion=simular_sin_conexion)
            ms = int((time.monotonic() - t0) * 1000)

            # c) Se guarda la respuesta sobre esa misma marca.
            self._cerrar_consulta(marca, resultado, ms)
            self._aplicar(resultado, marca)

    def reportar_error(self, sentido):
        """Lectura incompleta/timeout de la lectora -> código 3."""
        with self._lock:
            if self._en_cooldown(sentido):
                return
            self._marcar_lectura(sentido)
            self._aplicar(Resultado(3, MENSAJES[3], False, self._sentido_efectivo(sentido)), None)

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
            self._luz_ui(color)

    # ---- Aplicar resultado a hardware + UI + registro ----
    def _aplicar(self, resultado, marca=None):
        codigo = resultado.codigo

        if codigo == 1:                                  # HABILITADO
            # 1º el relé: el torniquete se libera primero.
            if self.ajustes:
                self.arduino.pulso(self.ajustes.comando_rele(resultado.sentido))
            else:
                self.arduino.pulso_rele(resultado.sentido)
            # 2º la luz verde, ya con el paso liberado.
            if config.RETARDO_RELE_LUZ_SEGUNDOS > 0:
                time.sleep(config.RETARDO_RELE_LUZ_SEGUNDOS)
            self.arduino.luz_verde()
            self._luz_ui("verde")
        elif codigo == 4:                                # SIN CONEXIÓN
            self.arduino.luz_amarilla()
            self.arduino.apagar_luz_despues(config.APAGAR_LUZ_AMARILLA_DESPUES)
            self._luz_ui("amarillo")
        else:                                            # 0, 2, 3
            self.arduino.luz_roja()
            self._luz_ui("rojo")

        log.info("Acceso %s | cód %d | %s | RUT %s",
                 resultado.sentido, codigo, resultado.mensaje, resultado.rut_norm)

        if self.ui:
            self.ui.mostrar_resultado(resultado)

        self._registrar(resultado, marca)
        self._programar_idle()

    # ---- Paso a: abrir la marca en la BD local ----
    def _abrir_marca(self, rut_norm, sentido):
        """Inserta la marca con RUT, evento y fecha/hora apenas se lee la cédula.
        Devuelve {'id_marca', 'id_evento'} o None (RUT ilegible o BD caída)."""
        if self.bd_local is None or rut_norm == "0":
            return None
        try:
            ubic = self.ajustes.ubicacion if self.ajustes else ""
            return self.bd_local.registrar_marca(
                rut=rut_norm,
                evento=sentido,
                fecha_hora=datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                rut_formateado=formatea_rut(rut_norm),
                ubicacion=ubic,
            )
        except Exception as e:  # noqa: BLE001
            log.error("No se pudo abrir la marca en la BD local: %s", e)
            return None

    # ---- Nombre del terminal ----
    def renombrar_terminal(self, nombre, usuario=None):
        """Cambia el nombre desde la pantalla de Ajustes.

        Escribe primero en la BD local —así el cambio se ve al instante, haya
        red o no— y despierta al sincronizador para subirlo enseguida. Si no
        hay conexión, queda pendiente con su fecha y se sube al reconectar.
        Devuelve el nombre que quedó, o None si no se pudo guardar.
        """
        if self.bd_local is None:
            log.error("Sin BD local: no se puede renombrar el terminal.")
            return None
        term = self.bd_local.renombrar_terminal(nombre, usuario=usuario)
        if not term:
            return None
        if self.sincronizador is not None:
            threading.Thread(target=self.sincronizador.sincronizar_nombre,
                             kwargs={"forzar": True}, daemon=True,
                             name="SubirNombre").start()
        return term.get("nombre")

    def nombre_terminal(self):
        """Nombre vigente del terminal según la BD local."""
        if self.bd_local is None:
            return None
        return (self.bd_local.terminal() or {}).get("nombre")

    def comprobar_api_externa(self):
        """Comprobación inicial: ¿responde la fuente que valida los RUT? Se
        llama al arrancar, antes de dar por sentado que está caída."""
        disponible = True
        error = None
        try:
            disponible = self.validador.disponible()
            if not disponible:
                error = getattr(self.validador, "error_carga", None) \
                    or "La fuente de validación no tiene datos"
        except Exception as e:  # noqa: BLE001
            disponible, error = False, str(e)
        self._estado_api_externa(disponible, error=error)
        return disponible

    # ---- Estado de conexión de la API externa ----
    def _estado_api_externa(self, responde, error=None):
        """La API externa respondió o no. Abre/cierra el incidente, lo deja en
        Errores y actualiza el indicador de la pantalla."""
        if self.bd_local is None:
            return
        try:
            self.bd_local.marcar_servicio("EXTERNA", responde, error=error)
            if responde:
                inc = self.bd_local.cerrar_incidente("EXTERNA")
                if inc:
                    dur = inc.get("duracion_segundos") or 0
                    log.info("Conexión con la API externa recuperada tras %s s (corte #%s).",
                             dur, inc["id"])
                    self.bd_local.registrar_error(
                        "api_externa", "Conexión con la API externa recuperada",
                        nivel="INFO",
                        detalle=(f"Corte #{inc['id']} detectado {inc['deteccion']}, "
                                 f"recuperado {inc['recuperacion']} ({dur} s)."))
            else:
                nuevo = self.bd_local.incidente_abierto("EXTERNA") is None
                id_inc = self.bd_local.abrir_incidente("EXTERNA", error=error)
                if nuevo and id_inc is not None:
                    log.error("Sin conexión con la API externa (corte #%s): %s", id_inc, error)
                    self.bd_local.registrar_error(
                        "api_externa", "Sin conexión con la API externa", nivel="ERROR",
                        detalle=f"Corte #{id_inc}. {error or ''}".strip())
        except Exception as e:  # noqa: BLE001
            log.error("No se pudo actualizar el estado de la API externa: %s", e)

        if self.ui:
            estado = self.bd_local.estado_servicio("EXTERNA") or {}
            self.ui.set_en_linea(responde, estado.get("ultima_conexion"), servicio="externa")

    # ---- Paso c: guardar la respuesta de la API externa ----
    def _cerrar_consulta(self, marca, resultado, duracion_ms=None):
        """Deja escrito qué respondió la API externa: habilitado 1/0, nombre y
        motivo. Código 4 = no respondió, se guarda como consulta fallida."""
        if self.bd_local is None or resultado.rut_norm == "0":
            return
        sin_respuesta = resultado.codigo == 4
        self._estado_api_externa(
            not sin_respuesta,
            error="La API externa no respondió" if sin_respuesta else None)
        try:
            self.bd_local.registrar_consulta_externa(
                rut_consultado=resultado.rut_norm,
                id_marca=marca["id_marca"] if marca else None,
                habilitado=None if sin_respuesta else resultado.autorizado,
                nombre=resultado.nombre,
                motivo=resultado.motivo,
                rut_respuesta=resultado.rut_norm if not sin_respuesta else None,
                exito=not sin_respuesta,
                mensaje_error="Sin conexión con la API externa" if sin_respuesta else None,
                duracion_ms=duracion_ms,
            )
        except Exception as e:  # noqa: BLE001
            log.error("No se pudo guardar la consulta externa: %s", e)

    def _registrar(self, resultado, marca=None):
        """Encola la marca (códigos 0 y 1) para subirla a BakeliteApi y deja el
        payload escrito en la BD local, para que los reintentos lo reenvíen tal
        cual, con el mismo IdEvento."""
        if self.store is None or resultado.codigo not in (0, 1):
            return
        try:
            ubic = self.ajustes.ubicacion if self.ajustes else ""
            ev = self.store.registrar(
                rut=resultado.rut_norm,   # sin enmascarar (el . y - son solo para el front)
                nombre=resultado.nombre,
                sentido=resultado.sentido,
                codigo=resultado.codigo,
                autorizado=resultado.autorizado,
                ubicacion=ubic,
                motivo=resultado.motivo,
                id_evento=marca["id_evento"] if marca else None,
                id_marca_local=marca["id_marca"] if marca else None,
            )
            if marca and self.bd_local is not None:
                self.bd_local.guardar_payload(marca["id_marca"], ev["payload"])
            if self.sincronizador is not None:
                self.sincronizador.notificar()
        except Exception as e:  # noqa: BLE001
            log.error("No se pudo registrar el evento: %s", e)

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
