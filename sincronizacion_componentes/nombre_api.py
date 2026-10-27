# -*- coding: utf-8 -*-
import datetime
import json
import logging
import time
import urllib.error
import urllib.request

import config
import terminal_auth
from sincronizacion_componentes.comun import _iso

log = logging.getLogger("sincronizador")

class NombreApiMixin:
    """Subida y transporte HTTP del nombre del terminal."""

    def _subir_nombre(self, term):
        """PUT .../desde-nuc — sube el nombre local con la fecha de su cambio."""
        fecha = _iso(term.get("nombre_fecha"))
        if not fecha:
            log.error("El terminal local no tiene NombreFecha; no se puede subir.")
            return None
        payload = {"nombre": term.get("nombre"), "nombreFecha": fecha,
                   "nombrePor": term.get("nombre_por")}
        code, cuerpo = self._llamar_nombre(
            "PUT", self._url_nombre(config.ENDPOINT_ACTUALIZAR_NOMBRE_TERMINAL), payload)
        if code != 200:
            return None
        try:
            d = json.loads(cuerpo)
        except Exception as e:  # noqa: BLE001
            log.error("Respuesta ilegible al subir el nombre: %s", e)
            return None

        estado = d.get("estado")
        if estado == "RECHAZADO_POR_ANTIGUEDAD":
            # Perdió la carrera: la API devuelve el nombre vigente y se adopta
            # aquí mismo. El conflicto queda cerrado sin una segunda vuelta.
            log.info("El cambio local perdió la carrera; se adopta %r de Bakelite.",
                     d.get("nombre"))
            self.bd_local.aplicar_nombre_remoto(d.get("nombre"), d.get("nombreFecha"))
            self._notificar_nombre(d.get("nombre"))
            return d.get("nombre")

        if estado in ("ACTUALIZADO", "SIN_CAMBIOS"):
            self.bd_local.marcar_nombre_sincronizado()
            if estado == "ACTUALIZADO":
                log.info("Nombre %r subido a Bakelite.", d.get("nombre"))
            return d.get("nombre")

        log.error("Estado desconocido al subir el nombre: %r", estado)
        return None

    def _llamar_nombre(self, metodo, url, payload=None):
        """Las tres llamadas del contrato comparten manejo de errores.

        Devuelve (código, cuerpo). El código es None si no hubo respuesta. Un
        400 o un 404 son definitivos: se bloquea el ciclo del nombre hasta el
        próximo arranque en vez de reintentar en bucle. El resto (429, 5xx,
        timeout, red) deja todo pendiente para el ciclo siguiente.
        """
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
        cabeceras = terminal_auth.cabeceras(
            {"Content-Type": "application/json"} if payload else None)
        req = urllib.request.Request(url, data=data, method=metodo, headers=cabeceras)
        try:
            with urllib.request.urlopen(req, timeout=config.API_TIMEOUT_SEGUNDOS) as resp:
                return resp.getcode(), resp.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as e:
            detalle = self._leer(e)
            if e.code == 404:
                msg = (f"El idTerminal {config.ID_TERMINAL} no existe en Bakelite: "
                       "el nombre no se puede sincronizar.")
                log.error(msg)
                self.bd_local.registrar_error("config", msg, nivel="CRITICO",
                                              detalle=detalle[:500])
                self._nombre_bloqueado = True
            elif e.code == 400:
                msg = f"Bakelite rechazó el nombre del terminal (400): {detalle[:300]}"
                log.error(msg)
                self.bd_local.registrar_error("config", msg, nivel="ERROR")
                self._nombre_bloqueado = True
            else:
                log.warning("HTTP %s sincronizando el nombre (%s %s).",
                            e.code, metodo, url)
            return e.code, detalle
        except Exception as e:  # noqa: BLE001
            log.warning("No se pudo sincronizar el nombre (%s %s): %s", metodo, url, e)
            return None, ""

    def _notificar_nombre(self, nombre):
        if self.on_nombre and nombre:
            try:
                self.on_nombre(nombre)
            except Exception as e:  # noqa: BLE001
                log.error("Error notificando el nombre del terminal: %s", e)

