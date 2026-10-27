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

class IncidentesApiMixin:
    """Aviso remoto de cortes de conectividad pendientes."""

    def _avisar_incidentes(self):
        """Informa los cortes recuperados usando ENDPOINT_REGISTRAR_INCIDENTE.

        El `idIncidente` es el UUID que creó la BD local al abrir el corte y se
        reenvía idéntico en cada reintento: la API deduplica por
        (idTerminal, idIncidente). Del terminal solo viaja su id, nada más.
        """
        if not config.USAR_BD_LOCAL or not config.ENDPOINT_REGISTRAR_INCIDENTE:
            return
        for inc in self.bd_local.incidentes_por_avisar():
            payload = self._payload_incidente(inc)
            if payload is None:
                continue
            if not self._enviar_incidente(inc, payload):
                return          # sin red: el resto espera al próximo ciclo

    def _payload_incidente(self, inc):
        """Arma el cuerpo del aviso y valida lo que exige el contrato. Devuelve
        None si el corte no es reportable (y lo deja marcado como tal)."""
        deteccion, recuperacion = inc["deteccion"], inc["recuperacion"]
        duracion = inc.get("duracion_segundos")
        if duracion is None and isinstance(deteccion, datetime.datetime) \
                and isinstance(recuperacion, datetime.datetime):
            duracion = int((recuperacion - deteccion).total_seconds())

        # El contrato exige recuperación posterior en al menos 1 segundo. Un
        # corte que empieza y termina dentro del mismo segundo sería rechazado
        # con 400, así que no se envía: queda en la BD local como NO_APLICA.
        if duracion is None or duracion < 1:
            self.bd_local.descartar_incidente(
                inc["id"], "Corte inferior a 1 segundo: no reportable según el contrato")
            log.info("Corte #%s descartado: duró menos de 1 segundo.", inc["id"])
            return None

        if not inc.get("uuid"):
            self.bd_local.descartar_incidente(inc["id"], "Incidente sin idIncidente (UUID)")
            log.error("Corte #%s sin UUID: no se puede informar.", inc["id"])
            return None

        return {
            "idIncidente": inc["uuid"],
            "idTerminal": config.ID_TERMINAL,
            "servicio": inc["servicio"],
            "fechaDeteccion": _iso(deteccion),
            "fechaRecuperacion": _iso(recuperacion),
            "duracionSegundos": duracion,
            "intentosFallidos": max(1, int(inc.get("intentos_fallidos") or 1)),
            "detalle": (inc.get("ultimo_error") or None),
        }

    def _enviar_incidente(self, inc, payload):
        """Devuelve False solo si se cayó la red (para cortar el ciclo)."""
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            config.ENDPOINT_REGISTRAR_INCIDENTE, data=data, method="POST",
            headers=terminal_auth.cabeceras({"Content-Type": "application/json"}))
        try:
            with urllib.request.urlopen(req, timeout=config.API_TIMEOUT_SEGUNDOS) as resp:
                cuerpo = resp.read().decode("utf-8", "ignore")
                code = resp.getcode()
            id_registro, estado = self._parse_incidente(cuerpo)
            # 201 REGISTRADO y 200 DUPLICADO cuentan igual: quedó entregado.
            self.bd_local.registrar_aviso_incidente(
                inc["id"], "OK", http_status=code, respuesta=cuerpo,
                id_registro_api=id_registro, estado_api=estado)
            log.info("Corte #%s informado a BakeliteApi (HTTP %s, %s, idRegistro=%s).",
                     inc["id"], code, estado, id_registro)
            return True
        except urllib.error.HTTPError as e:
            detalle = self._leer(e)
            if e.code == 400:
                self.bd_local.registrar_aviso_incidente(
                    inc["id"], "PERMANENTE", http_status=e.code, respuesta=detalle,
                    error=f"HTTP 400: {detalle[:300]}")
                log.error("Corte #%s rechazado por la API (400): %s", inc["id"], detalle[:300])
            else:
                self.bd_local.registrar_aviso_incidente(
                    inc["id"], "REINTENTAR", http_status=e.code, respuesta=detalle,
                    error=f"HTTP {e.code}: {detalle[:300]}")
            return True
        except Exception as e:  # noqa: BLE001
            self.bd_local.registrar_aviso_incidente(inc["id"], "REINTENTAR",
                                                    error=f"Fallo de red: {e}")
            return False

    @staticmethod
    def _parse_incidente(cuerpo):
        try:
            d = json.loads(cuerpo)
            return d.get("idRegistro"), d.get("estado")
        except Exception:  # noqa: BLE001
            return None, None

