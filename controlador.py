# -*- coding: utf-8 -*-
"""
Orquestador: une lectora -> validación -> luz + relé -> pantalla -> registro.

Flujo de un acceso (§7):
  1. inicio_lectura(numero): al primer byte enciende AZUL (relé + pantalla) y
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
from depurador import depurador
from rut import fn_enmascara_rut, formatea_rut
from validador import Resultado, MENSAJES

log = logging.getLogger("controlador")

# Cuánto se ignora lo que llegue de una lectora recién identificada: cubre el
# resto del escaneo en curso (la trama completa, o el timeout de lectura
# incompleta) sin tragarse un acceso real hecho después.
IDENTIFICAR_DESCARTE_SEGUNDOS = 5.0


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
        self.dispositivos = None     # lo enchufa main al arrancar
        self._lock = threading.Lock()
        self._timer_idle = None
        self._cooldown = {}   # número de lectora -> time.monotonic() del último proceso
        self._ultimo_procesado = {}   # número de lectora -> último RUT consultado
        self._ultimo_rut = {}  # número de lectora -> (rut, time.monotonic())
        self._identificando = None   # callback(numero) mientras se identifica
        self._descartar = {}         # numero -> hasta cuándo se ignora lo que llegue
        self._ocupada = {}           # número de lectora -> cuándo empezó su trámite
        self._ui_consultando = False  # la pantalla quedó mostrando "consultando"

    # ---- Anti-doble-lectura ----
    # La clave es el NÚMERO de lectora, no el sentido: cambiar cuál es entrada
    # no debe arrastrar el cooldown de la otra.
    def _en_cooldown(self, numero):
        t = self._cooldown.get(numero, 0.0)
        return (time.monotonic() - t) < config.LECTURA_COOLDOWN_SEGUNDOS

    def _marcar_lectura(self, numero):
        self._cooldown[numero] = time.monotonic()

    def _anotar_lectura(self, numero, rut):
        """Deja constancia de que la cédula sigue sobre el lector, sin procesarla."""
        if rut != "0":
            self._ultimo_rut[numero] = (rut, time.monotonic())

    def _repetida(self, numero, rut):
        """¿Es la misma cédula que sigue apoyada sobre el lector?

        Lo que decide es la PAUSA entre lecturas, no cuánto rato pasó desde la
        primera. Apoyada, la lectora repite cada pocas décimas y no se vuelve a
        consultar por mucho que se quede ahí. Si la persona la retira y la pasa
        de nuevo, ese silencio hace que cuente como pasada nueva y se consulta
        enseguida, sin esperas raras.
        """
        if rut == "0":
            return False
        anterior, t = self._ultimo_rut.get(numero, (None, 0.0))
        ahora = time.monotonic()
        repetida = (anterior == rut and
                    (ahora - t) < config.LECTURA_PAUSA_REINICIO_SEGUNDOS)
        self._ultimo_rut[numero] = (rut, ahora)
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

    # ---- Identificación de lectoras ----
    # Las lectoras son escáneres: solo leen, no hay comando para hacerlas
    # parpadear. Para saber cuál es cuál se usa lo único que sí hacen: leer.
    # Con el modo activo, el primer escaneo que llegue delata su lectora y no
    # se procesa como marca (no abre torniquete ni registra nada).
    def iniciar_identificacion(self, callback):
        """callback(numero, rut) se llama con la lectora que haya leído.

        `rut` viene formateado para pantalla, o None si la lectura fue inválida
        (igual sirve para identificar: delata la lectora aunque no se entienda).
        """
        with self._lock:
            self._identificando = callback
        log.info("Modo identificación de lectoras ACTIVO.")

    def cancelar_identificacion(self):
        with self._lock:
            self._identificando = None
        log.info("Modo identificación de lectoras cancelado.")

    def _identificar(self, numero, rut=None):
        """Devuelve True si la lectura se consumió identificando."""
        if self._descartando(numero):
            return True
        cb = self._identificando
        if cb is None:
            return False
        self._identificando = None
        # El modo se consume con el PRIMER BYTE, pero la trama completa llega
        # unos milisegundos después. Sin esta ventana, esa trama entraría como
        # un acceso normal: abriría el torniquete, guardaría la marca y
        # consultaría a la API externa. Identificar no debe hacer nada de eso.
        self._descartar[numero] = time.monotonic() + IDENTIFICAR_DESCARTE_SEGUNDOS
        log.info("Lectora %s identificada por escaneo (su lectura se descarta).",
                 numero)
        try:
            cb(numero, rut)
        except Exception as e:  # noqa: BLE001
            log.error("Error en el callback de identificación: %s", e)
        return True

    def _lectora_ocupada(self, numero):
        """¿Esta lectora tiene un trámite en curso?

        Si el trámite lleva más de lo que puede tardar en el peor caso, algo
        quedó colgado —una consulta que nunca volvió, la BD trabada— y se libera
        por la fuerza. Es la diferencia entre perder una lectura y que la lectora
        quede muerta hasta el próximo reinicio.
        """
        desde = self._ocupada.get(numero)
        if desde is None:
            return False
        if (time.monotonic() - desde) < config.OCUPADA_MAX_SEGUNDOS:
            return True
        self._ocupada.pop(numero, None)
        self._ui_consultando = False
        msg = (f"La lectora {numero} quedó ocupada más de "
               f"{config.OCUPADA_MAX_SEGUNDOS} s: se liberó por la fuerza para "
               "poder seguir atendiendo.")
        log.error(msg)
        depurador.respuesta(msg, origen="controlador")
        if self.bd_local is not None:
            try:
                self.bd_local.registrar_error("controlador", msg, nivel="ERROR")
            except Exception:  # noqa: BLE001
                log.exception("No se pudo registrar la liberación forzada de la lectora.")
        try:
            self.arduino.apagar_luz()
        except Exception:  # noqa: BLE001
            log.exception("No se pudo apagar la luz tras liberar la lectora.")
        if self.ui:
            self.ui.mostrar_esperando()
        return False

    def _descartando(self, numero):
        """¿Lo que llegue de esta lectora es la cola de una identificación?"""
        hasta = self._descartar.get(numero, 0.0)
        if time.monotonic() >= hasta:
            self._descartar.pop(numero, None)
            return False
        return True

    # ---- Llamado por la lectora ----
    def inicio_lectura(self, numero=1):
        """Primer byte de una lectura.

        No pinta nada, a propósito. Antes encendía el azul y ponía
        "consultando" aquí, pero en el primer byte todavía no se sabe si esa
        lectura se va a procesar: una cédula apoyada sobre el lector manda
        ráfagas sin parar y casi todas se descartan por repetidas. El resultado
        era una pantalla que decía "consultando" para una consulta que nunca
        existió, y que se quedaba así hasta la lectura siguiente.

        Ahora el azul lo enciende procesar_trama, justo antes de consultar, así
        el azul y el mensaje duran exactamente lo que dura la consulta.
        """
        return

    def _empezar_consulta(self, sentido):
        """Enciende el azul y muestra "consultando". Solo se llama cuando la
        consulta va de verdad, así siempre hay un resultado que lo reemplace."""
        self._cancelar_idle()
        self.arduino.luz_azul()             # L1B*
        self._luz_ui("azul")
        self._ui_consultando = True
        if self.ui:
            self.ui.mostrar_consultando(sentido)

    def _sentido_efectivo(self, numero):
        """El sentido lo manda la configuración de la BD, por número de lectora."""
        if self.ajustes:
            return self.ajustes.sentido_lectora(numero)
        return config.SENTIDO_LECTORA1 if numero == 1 else config.SENTIDO_LECTORA2

    def procesar_trama(self, trama, numero, sentido=None, simular_sin_conexion=False):
        # Mientras hay una consulta en curso, lo que siga llegando de esa
        # lectora es la misma cédula que sigue apoyada. Se anota que se la vio
        # —para que al terminar la consulta no parezca una pasada nueva— y se
        # descarta sin tocar el lock. Encolarlas era el error: se quedaban
        # esperando 7 segundos y salían todas juntas como si fueran cédulas
        # recién presentadas.
        if self._lectora_ocupada(numero):
            self._anotar_lectura(numero, fn_enmascara_rut(trama))
            return

        with self._lock:
            if self._identificando is not None or self._descartando(numero):
                self._identificar(numero, formatea_rut(fn_enmascara_rut(trama)) or None)
                return
            # El orden importa: PRIMERO se anota la lectura, después se decide
            # si se procesa. Con el cooldown adelante, las lecturas que él
            # descartaba no llegaban a anotarse y el detector de repetición se
            # quedaba ciego: cada 2 s creía ver una cédula nueva y volvía a
            # consultar aunque fuera la misma apoyada sobre el lector.
            rut_norm = fn_enmascara_rut(trama)
            if self._repetida(numero, rut_norm):
                log.debug("La misma cédula sigue sobre la lectora %s (RUT %s).",
                          numero, rut_norm)
                return

            # El cooldown existe para que un mismo escaneo no dispare dos
            # consultas. No debe frenar a la persona siguiente: en un torniquete
            # la gente pasa una detrás de otra, y una cédula distinta es un
            # acceso distinto que merece respuesta ya.
            otra_persona = (rut_norm != "0" and
                            rut_norm != self._ultimo_procesado.get(numero))
            if self._en_cooldown(numero) and not otra_persona:
                log.info("Lectura ignorada por cooldown (lectora %s)", numero)
                return
            self._marcar_lectura(numero)
            self._ultimo_procesado[numero] = rut_norm
            s_ef = self._sentido_efectivo(numero)
            depurador.respuesta(
                f"Lectora {numero} ({'ENTRADA' if s_ef == 'E' else 'SALIDA'}) leyó "
                f"el RUT {formatea_rut(rut_norm) or rut_norm}", origen="lectora")
            if self.bd_local is not None:
                self.bd_local.registrar_lectura(numero)

            # De aquí hasta el resultado, esta lectora queda tomada: mientras
            # su luz esté azul no se valida nada más en ella. El finally del
            # trámite es lo que garantiza que se libere pase lo que pase.
            self._ocupada[numero] = time.monotonic()

        # El trámite sale del hilo de la lectora. Si se hiciera aquí, ese hilo
        # quedaría bloqueado hasta 7 s sin vaciar el puerto: la lectora dejaría
        # de leer y al volver procesaría de golpe todo lo acumulado. Así sigue
        # leyendo, y lo que llegue mientras tanto se descarta por `_ocupada`.
        threading.Thread(target=self._tramitar, daemon=True, name=f"Acceso{numero}",
                         args=(rut_norm, s_ef, numero, simular_sin_conexion)).start()

    def _tramitar(self, rut_norm, s_ef, numero, simular_sin_conexion=False):
        """Marca, consulta y resultado. Corre fuera del hilo de la lectora."""
        try:
            # a) La cédula ya pasó: se guarda la marca antes de preguntar nada.
            marca = self._abrir_marca(rut_norm, s_ef)

            # b) Consulta a la API externa (hoy la resuelve el Validador).
            self._empezar_consulta(s_ef)
            t0 = time.monotonic()
            depurador.accion(f"Consultar acceso del RUT {rut_norm}", origen="api")
            resultado = self._validar_con_limite(rut_norm, s_ef, simular_sin_conexion)
            ms = int((time.monotonic() - t0) * 1000)
            depurador.respuesta(
                f"Respuesta en {ms} ms: código {resultado.codigo} · "
                f"{resultado.mensaje}", origen="api")

            # c) Se guarda la respuesta sobre esa misma marca. La pantalla y el
            #    semáforo son uno solo, así que esto sí va serializado.
            with self._lock:
                self._cerrar_consulta(marca, resultado, ms)
                self._aplicar(resultado, marca)
        except Exception as e:  # noqa: BLE001
            log.error("Error tramitando el acceso (lectora %s): %s", numero, e)
        finally:
            self._ocupada.pop(numero, None)

    def _validar_con_limite(self, rut_norm, sentido, simular_sin_conexion=False):
        """Consulta con tope de tiempo.

        La luz azul y el "consultando" duran exactamente lo que tarde la
        respuesta, porque esta llamada es la que los mantiene. Si la API no
        contesta dentro de VALIDACION_TIMEOUT_SEGUNDOS se corta el trámite y se
        le pide a la persona que vuelva a intentar: dejar la pantalla colgada
        indefinidamente es peor que decirle que reintente.

        La consulta corre en un hilo aparte para poder abandonarla. Si contesta
        tarde, esa respuesta ya no sirve —a la persona se le dijo otra cosa— y
        se descarta.
        """
        caja = {}

        def consultar():
            try:
                caja["r"] = self.validador.validar(
                    rut_norm, sentido, simular_sin_conexion=simular_sin_conexion)
            except Exception as e:  # noqa: BLE001
                caja["error"] = e

        hilo = threading.Thread(target=consultar, daemon=True, name="ValidarAcceso")
        hilo.start()
        hilo.join(config.VALIDACION_TIMEOUT_SEGUNDOS)

        if "r" in caja:
            return caja["r"]
        if "error" in caja:
            # La consulta falló (red, servicio caído): eso es "sin conexión".
            log.error("La consulta de acceso falló: %s", caja["error"])
            return Resultado(4, MENSAJES[4], False, sentido, rut_norm)

        log.warning("La API externa no respondió en %s s (RUT %s): se pide reintentar.",
                    config.VALIDACION_TIMEOUT_SEGUNDOS, rut_norm)
        return Resultado(5, MENSAJES[5], False, sentido, rut_norm)

    def reportar_error(self, numero, sentido=None):
        """Lectura incompleta/timeout de la lectora -> código 3.

        Una lectura fallida igual sirve para identificar: delata la lectora."""
        with self._lock:
            if self._identificar(numero):
                return
            if self._en_cooldown(numero):
                return
            self._marcar_lectura(numero)
            if self._lectora_ocupada(numero):
                return      # hay una consulta en curso: no se pisa su resultado
            self._aplicar(Resultado(3, MENSAJES[3], False,
                                    self._sentido_efectivo(numero)), None)

    def probar_rele(self, sentido_logico):
        """Dispara el relé correspondiente a un sentido lógico (para calibrar)."""
        cmd = self.ajustes.comando_rele(sentido_logico) if self.ajustes \
            else (config.RELE1 if sentido_logico == "E" else config.RELE2)
        log.info("Prueba de relé %s -> %s", sentido_logico, cmd)
        self.arduino.pulso(cmd)

    def probar_rele_numero(self, numero):  # noqa: D401
        """Dispara un relé por su número físico, sin importar qué sentido tenga.

        Es lo que permite reconocerlo: se acciona el relé 1, se mira qué
        torniquete se abrió, y recién ahí se decide si es entrada o salida.
        """
        cmd = (self.ajustes.comando_de_rele(numero) if self.ajustes
               else (config.RELE1 if numero == 1 else config.RELE2))
        log.info("Prueba del relé %s -> %s", numero, cmd)
        self.arduino.pulso(cmd)
        if self.bd_local is not None:
            self.bd_local.registrar_disparo_rele(numero)
        return cmd

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
        self._ui_consultando = False

        if codigo == 1:                                  # HABILITADO
            # 1º el relé: el torniquete se libera primero.
            if self.ajustes:
                self.arduino.pulso(self.ajustes.comando_rele(resultado.sentido))
                numero_rele = self.ajustes.numero_rele(resultado.sentido)
            else:
                self.arduino.pulso_rele(resultado.sentido)
                numero_rele = 1 if resultado.sentido == "E" else 2
            if self.bd_local is not None and numero_rele:
                self.bd_local.registrar_disparo_rele(numero_rele)
            # 2º la luz verde, ya con el paso liberado.
            if config.RETARDO_RELE_LUZ_SEGUNDOS > 0:
                time.sleep(config.RETARDO_RELE_LUZ_SEGUNDOS)
            self.arduino.luz_verde()
            self._luz_ui("verde")
        elif codigo in (4, 5):                           # SIN CONEXIÓN / SIN RESPUESTA
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

    def notificar_dispositivos(self):
        """Algo cambió en la configuración de lectoras o relés: se informa a
        Bakelite al instante en vez de esperar el ciclo."""
        if self.dispositivos is not None:
            self.dispositivos.notificar()

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
        # Se simula por sentido, así que se busca qué lectora lo cumple hoy.
        numero = (self.ajustes.numero_lectora(sentido) if self.ajustes else None) \
            or (1 if sentido == "E" else 2)

        def worker():
            self.inicio_lectura(numero)
            if error_lectura:
                trama = "BASURA-SIN-FORMATO-XYZ"
            else:
                trama = f"?RUN={valor}&SEC=1"
            self.procesar_trama(trama, numero, simular_sin_conexion=sin_conexion)

        threading.Thread(target=worker, daemon=True).start()
