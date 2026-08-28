# -*- coding: utf-8 -*-
"""
Heartbeat: le avisa a Bakelite que esta aplicación está funcionando.

Contrato: CONTRATO_DISPONIBILIDAD_SOFTWARE_ESCRITORIO.md

La idea del contrato es que Python NO opina sobre su propio estado. Manda una
señal vacía cada 10 segundos y la API, con su reloj, decide si el terminal está
EN_LINEA o SIN_CONEXION. Por eso aquí no se envía ninguna fecha ni se calcula
nada: solo se avisa y se obedece lo que responda.

Presencia NO es salud del hardware. Que el heartbeat llegue solo dice que este
proceso corre y alcanza la red; las lectoras y el Arduino tienen su propio
indicador en la pantalla.

Va en su propio hilo, separado del sincronizador de marcas, porque su cadencia
es fija: si se colgara detrás de una subida lenta, la web mostraría una caída
que no existe.
"""

import time
import logging
import threading
import urllib.request
import urllib.error
import json

import config
from depurador import depurador

log = logging.getLogger("presencia")

# Cuántos 404 seguidos hacen falta para creer que el terminal de verdad no
# existe. Un servidor con la API sin publicar responde 404 a todo, y ese caso
# no es un error de configuración: es la API caída.
AVISAR_TRAS_404_SEGUIDOS = 3


class Heartbeat(threading.Thread):
    def __init__(self, bd_local=None, on_critico=None, on_estado=None,
                 on_resuelto=None):
        super().__init__(daemon=True, name="Heartbeat")
        self.bd_local = bd_local
        self.on_critico = on_critico      # callback(texto) para avisarle al operador
        self.on_resuelto = on_resuelto    # callback() cuando el problema se corrigió
        self.on_estado = on_estado        # callback(estado: str|None)
        self.estado = None                # último estado que informó la API
        self.intervalo = config.HEARTBEAT_INTERVALO_SEGUNDOS
        self.detenido_por_inactivo = False
        self._detener_evento = threading.Event()
        self._fallos = 0                  # fallos seguidos, para no llenar el log
        self._404_seguidos = 0            # ver _es_config_rota()
        self._avisado = False             # ¿hay un cartel en pantalla?

    def detener(self):
        self._detener_evento.set()

    @property
    def url(self):
        return config.ENDPOINT_HEARTBEAT_TERMINAL.format(id=config.ID_TERMINAL)

    def run(self):
        # Al iniciar se manda de inmediato: el contrato pide que la web vea la
        # conexión sin esperar el primer ciclo.
        while not self._detener_evento.is_set():
            comienzo = time.monotonic()
            try:
                espera = self._latir()
            except Exception as e:  # noqa: BLE001
                log.error("Error inesperado en el heartbeat: %s", e)
                espera = self.intervalo
            if espera is None:            # 409: el terminal está inactivo
                return
            # Se descuenta lo que tardó la llamada: si tardó 4 s, se esperan 6,
            # no 10. Así el ritmo es de verdad uno cada `intervalo` segundos y
            # no se acumulan peticiones atrasadas.
            resto = max(0.0, espera - (time.monotonic() - comienzo))
            self._detener_evento.wait(timeout=resto)

    def _latir(self):
        """Manda un heartbeat. Devuelve cuántos segundos esperar, o None si hay
        que dejar de latir para siempre."""
        peticion = urllib.request.Request(self.url, data=b"", method="POST")
        try:
            with urllib.request.urlopen(
                    peticion, timeout=config.HEARTBEAT_TIMEOUT_SEGUNDOS) as resp:
                cuerpo = resp.read().decode("utf-8", "ignore")
            return self._aceptado(cuerpo)
        except urllib.error.HTTPError as e:
            return self._rechazado(e)
        except Exception as e:  # noqa: BLE001
            # Fallo temporal: la aplicación sigue operando igual. El acceso de
            # una persona no depende de que Bakelite sepa que estamos vivos.
            self._fallos += 1
            if self._fallos == 1 or self._fallos % 30 == 0:
                log.warning("Sin poder avisar presencia (%d seguidos): %s",
                            self._fallos, e)
            self._anunciar(None)
            return self.intervalo

    def _aceptado(self, cuerpo):
        log.debug("Heartbeat aceptado por Bakelite: %s", cuerpo[:500])
        if self._fallos:
            log.info("Presencia restablecida tras %d intentos fallidos.", self._fallos)
        self._fallos = 0
        self._resolver_aviso()
        datos = {}
        try:
            datos = json.loads(cuerpo) if cuerpo else {}
        except Exception as e:  # noqa: BLE001
            log.warning("Heartbeat respondió JSON inválido: %s (%s)", cuerpo[:500], e)

        # El ritmo lo manda la API, no este archivo: si algún día cambia el
        # intervalo, el terminal lo adopta solo.
        intervalo = datos.get("heartbeatCadaSegundos")
        if isinstance(intervalo, (int, float)) and intervalo > 0:
            if intervalo != self.intervalo:
                log.info("La API pide un heartbeat cada %s s.", intervalo)
            self.intervalo = float(intervalo)

        estado = datos.get("estado")
        if estado != self.estado:
            log.info("Presencia: %s", estado)
            depurador.respuesta(f"Presencia aceptada: {estado}", origen="heartbeat")
        self._anunciar(estado)

        recuperado = datos.get("idIncidenteRecuperado")
        if recuperado:
            # La API detectó que estuvimos ausentes y este latido cerró su
            # incidente. Es una caída vista desde el otro lado, distinta de las
            # que registramos nosotros en dbo.IncidentesConexion.
            log.info("Bakelite cerró el incidente de ausencia #%s.", recuperado)
            depurador.respuesta(
                f"La API cerró su incidente de ausencia #{recuperado}",
                origen="heartbeat")
            self._registrar("api", f"Bakelite cerró el incidente de ausencia "
                                   f"#{recuperado} al recuperar la presencia.",
                            nivel="INFO")
        return self.intervalo

    def _rechazado(self, error):
        codigo = error.code
        detalle = self._leer(error)

        if codigo == 409:
            # El terminal fue dado de baja: seguir latiendo no tiene sentido y
            # el contrato exige avisarle al operador.
            texto = (f"El terminal {config.ID_TERMINAL} está INACTIVO en Bakelite: "
                     "se dejó de informar presencia.")
            log.error(texto)
            self.detenido_por_inactivo = True
            self._registrar("config", texto, nivel="CRITICO", detalle=detalle)
            self._anunciar("INACTIVO")
            self._avisar(texto)
            return None

        if codigo == 404:
            texto = (f"El idTerminal {config.ID_TERMINAL} no existe en Bakelite: "
                     "no se puede informar presencia.")
            self._404_seguidos += 1
            if self._404_seguidos == 1:
                log.error(texto)
                self._registrar("config", texto, nivel="CRITICO", detalle=detalle)
            if self._es_config_rota() and not self._avisado:
                # Se repitió lo suficiente: ahora sí es un problema de
                # configuración y el operador tiene que verlo.
                self._avisar(texto)
            self._fallos += 1
            self._anunciar(None)
            return config.HEARTBEAT_ESPERA_ERROR_SEGUNDOS

        if codigo == 429:
            espera = self._retry_after(error) or config.HEARTBEAT_ESPERA_ERROR_SEGUNDOS
            log.warning("Límite de peticiones en el heartbeat; se reintenta en %s s.",
                        espera)
            self._anunciar(None)
            return espera

        self._fallos += 1
        if self._fallos == 1 or self._fallos % 30 == 0:
            log.warning("El heartbeat devolvió HTTP %s (%d seguidos).",
                        codigo, self._fallos)
        self._anunciar(None)
        return self.intervalo

    # ---- Auxiliares ----
    @staticmethod
    def _retry_after(error):
        try:
            return max(1.0, float(error.headers.get("Retry-After")))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _leer(error):
        try:
            return error.read().decode("utf-8", "ignore")[:500]
        except Exception:  # noqa: BLE001
            return ""

    def _anunciar(self, estado):
        self.estado = estado
        if self.on_estado:
            try:
                self.on_estado(estado)
            except Exception as e:  # noqa: BLE001
                log.error("Error notificando la presencia: %s", e)

    def _es_config_rota(self):
        """¿Este 404 es de verdad un terminal inexistente?

        Un 404 no siempre significa eso. Cuando la API no está publicada, el
        servidor responde 404 a CUALQUIER ruta, y al arrancar sin conexión el
        terminal mostraba en pantalla "el idTerminal no existe" — un error de
        configuración que no era tal, y que además no se iba solo al volver la
        API. Se exige que se repita para creerle.
        """
        return self._404_seguidos >= AVISAR_TRAS_404_SEGUIDOS

    def _resolver_aviso(self):
        """La API respondió: si había un cartel en pantalla, se retira."""
        self._404_seguidos = 0
        if self._avisado:
            self._avisado = False
            if self.on_resuelto:
                try:
                    self.on_resuelto()
                except Exception as e:  # noqa: BLE001
                    log.error("Error retirando el aviso: %s", e)

    def _avisar(self, texto):
        self._avisado = True
        if self.on_critico:
            try:
                self.on_critico(texto)
            except Exception as e:  # noqa: BLE001
                log.error("Error avisando al operador: %s", e)

    def _registrar(self, origen, mensaje, nivel="ERROR", detalle=None):
        if self.bd_local is None or not config.USAR_BD_LOCAL:
            return
        try:
            self.bd_local.registrar_error(origen, mensaje, nivel=nivel,
                                          detalle=detalle)
        except Exception as e:  # noqa: BLE001
            log.error("No se pudo registrar el error de presencia: %s", e)
