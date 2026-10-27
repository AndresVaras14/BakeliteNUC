# -*- coding: utf-8 -*-
import datetime
import json
import logging
import time
import urllib.error
import urllib.request

import config
from sincronizacion_componentes.comun import _iso

log = logging.getLogger("sincronizador")

class NombreTerminalMixin:
    """Resolución bidireccional del nombre del terminal."""

    @staticmethod
    def _url_nombre(plantilla):
        return plantilla.format(id=config.ID_TERMINAL) if plantilla else None

    def _sincronizar_nombre_si_toca(self):
        """Ritmo propio, mucho más lento que el de las marcas: el nombre no
        cambia seguido y la comparación no escribe nada."""
        if (time.monotonic() - self._ultimo_nombre_sync) < config.NOMBRE_SYNC_INTERVALO_SEGUNDOS:
            return
        self.sincronizar_nombre()

    def sincronizar_nombre(self, forzar=False):
        """Deja el nombre igual en la BD local y en Bakelite.

        Si hay un cambio local sin subir va directo al PUT; si no, compara y
        actúa según el veredicto. Devuelve el nombre vigente, o None si no se
        pudo resolver (sin red, sin BD o bloqueado por un error definitivo).
        """
        if not config.USAR_BD_LOCAL or self._nombre_bloqueado:
            return None
        if not (config.ENDPOINT_COMPARAR_NOMBRE_TERMINAL
                and config.ENDPOINT_OBTENER_NOMBRE_TERMINAL
                and config.ENDPOINT_ACTUALIZAR_NOMBRE_TERMINAL):
            return None
        if not forzar and self.en_linea is False:
            return None            # sin red: el nombre local manda en pantalla

        term = self.bd_local.terminal()
        if not term:
            log.warning("No se pudo leer el terminal de la BD local; nombre sin sincronizar.")
            return None
        self._ultimo_nombre_sync = time.monotonic()

        # Un cambio local pendiente no necesita comparar: el PUT ya resuelve el
        # conflicto en una sola llamada (lo aplica o devuelve el nombre vigente).
        if not term.get("nombre_sincronizado"):
            return self._subir_nombre(term)

        veredicto = self._comparar_nombre(term)
        if veredicto == "ACTUALIZAR_LOCAL":
            return self._bajar_nombre()
        if veredicto == "ACTUALIZAR_API":
            return self._subir_nombre(term)
        if veredicto == "IGUALES":
            return term.get("nombre")
        return None

    def _comparar_nombre(self, term):
        """POST .../comparar — no escribe en ningún lado. Devuelve el veredicto."""
        fecha = _iso(term.get("nombre_fecha"))
        if not fecha:
            log.error("El terminal local no tiene NombreFecha; no se puede comparar.")
            return None
        payload = {"nombre": term.get("nombre"), "nombreFecha": fecha,
                   "nombreOrigen": "LOCAL"}
        code, cuerpo = self._llamar_nombre(
            "POST", self._url_nombre(config.ENDPOINT_COMPARAR_NOMBRE_TERMINAL), payload)
        if code != 200:
            return None
        try:
            d = json.loads(cuerpo)
        except Exception as e:  # noqa: BLE001
            log.error("Respuesta ilegible al comparar el nombre: %s", e)
            return None
        veredicto = d.get("veredicto")
        if veredicto not in ("IGUALES", "ACTUALIZAR_LOCAL", "ACTUALIZAR_API"):
            log.error("Veredicto desconocido al comparar el nombre: %r", veredicto)
            return None
        if veredicto != "IGUALES":
            log.info("Nombre del terminal — local: %r · Bakelite: %r → %s",
                     (d.get("local") or {}).get("nombre"),
                     (d.get("api") or {}).get("nombre"), veredicto)
        elif not term.get("nombre_sincronizado"):
            self.bd_local.marcar_nombre_sincronizado()
        return veredicto

    def _bajar_nombre(self):
        """GET .../hacia-nuc — adopta el nombre de Bakelite con SU fecha."""
        code, cuerpo = self._llamar_nombre(
            "GET", self._url_nombre(config.ENDPOINT_OBTENER_NOMBRE_TERMINAL))
        if code != 200:
            return None
        try:
            d = json.loads(cuerpo)
        except Exception as e:  # noqa: BLE001
            log.error("Respuesta ilegible al bajar el nombre: %s", e)
            return None
        if self.bd_local.aplicar_nombre_remoto(d.get("nombre"), d.get("nombreFecha"),
                                               d.get("nombrePor")):
            self._notificar_nombre(d.get("nombre"))
            return d.get("nombre")
        # El UPDATE no tocó nada: el nombre local ya era más nuevo. Lo resuelve
        # el PUT del ciclo siguiente; no se pisa nada.
        log.info("El nombre local es más nuevo que el de Bakelite: no se adopta.")
        return None

