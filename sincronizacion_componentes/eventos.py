# -*- coding: utf-8 -*-
import datetime
import json
import logging
import time
import urllib.error
import urllib.request

import config
import terminal_auth

log = logging.getLogger("sincronizador")

class EventosMixin:
    """Entrega durable de marcas a SQL Server y BakeliteApi."""

    def _subir_local(self, ev):
        """Normalmente la marca ya está en la BD: la creó el controlador al
        pasar la cédula. Esto cubre el caso contrario —BD caída en ese momento—
        reconstruyéndola con el mismo IdEvento. Devuelve el IdMarca o None."""
        if not config.USAR_BD_LOCAL:
            return None
        id_marca = ev.get("id_marca_local") or self.bd_local.recuperar_marca(ev)
        if id_marca is None:
            log.warning("Marca %s no se pudo guardar en la BD local; queda pendiente.",
                        ev.get("id_evento"))
        return id_marca

    def _subir_api(self, ev):
        """Devuelve 'ok', 'permanente' o 'reintentar'. Marca la cola y deja
        el intento registrado en la BD local (tor.EnviosApi)."""
        if config.SIMULAR_API:
            self.store.marcar(ev["id"], api=True)
            self._registrar_envio(ev, "OK", estado_api="SIMULADO")
            self._marcar_online()
            return "ok"
        if not config.ENDPOINT_REGISTRAR_EVENTO:
            return "reintentar"

        payload = ev.get("payload")
        if not payload:
            self.store.marcar(ev["id"], api=-1, extra={"api_error": "sin payload"})
            self._registrar_envio(ev, "PERMANENTE", mensaje_error="sin payload")
            return "permanente"

        cuerpo_envio = json.dumps(payload, ensure_ascii=False)
        req = urllib.request.Request(
            config.ENDPOINT_REGISTRAR_EVENTO,
            data=cuerpo_envio.encode("utf-8"), method="POST",
            headers=terminal_auth.cabeceras({"Content-Type": "application/json"}))
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=config.API_TIMEOUT_SEGUNDOS) as resp:
                code = resp.getcode()
                cuerpo = resp.read().decode("utf-8", "ignore")
            ms = int((time.monotonic() - t0) * 1000)
            self._marcar_online()
            idmarca, estado = self._parse(cuerpo)
            self.store.marcar(ev["id"], api=True, extra={"idMarca": idmarca})
            self._registrar_envio(ev, "OK", http_status=code, request_json=cuerpo_envio,
                                  respuesta_json=cuerpo, estado_api=estado,
                                  id_marca_api=idmarca, duracion_ms=ms)
            log.info("Marca %s entregada (HTTP %s, %s) idMarca=%s",
                     ev["id_evento"], code, estado, idmarca)
            return "ok"
        except urllib.error.HTTPError as e:
            ms = int((time.monotonic() - t0) * 1000)
            detalle = self._leer(e)
            # Mismo criterio que el ping: un 404 o un 5xx aquí es la API caída,
            # no "el servidor contestó algo". Si no, cada reintento de una marca
            # pendiente contra una API caída la daba por recuperada.
            if self._api_viva(e.code):
                self._marcar_online()
            else:
                self._marcar_offline(f"HTTP {e.code} al enviar la marca")
            if e.code == 400:
                self.store.marcar(ev["id"], api=-1,
                                  extra={"api_error": f"HTTP 400: {detalle[:300]}"})
                self._registrar_envio(ev, "PERMANENTE", http_status=e.code,
                                      request_json=cuerpo_envio, respuesta_json=detalle,
                                      mensaje_error=f"HTTP 400: {detalle[:300]}",
                                      duracion_ms=ms)
                self._registrar_error("api", f"Marca {ev['id_evento']} rechazada (400)",
                                      detalle=detalle)
                log.error("Marca %s rechazada por la API (400): %s",
                          ev["id_evento"], detalle[:300])
                return "permanente"
            # 429 / 5xx / otros -> reintentar
            self._registrar_envio(ev, "REINTENTAR", http_status=e.code,
                                  request_json=cuerpo_envio, respuesta_json=detalle,
                                  mensaje_error=f"HTTP {e.code}: {detalle[:300]}",
                                  duracion_ms=ms)
            log.warning("Marca %s: HTTP %s, se reintenta. %s",
                        ev["id_evento"], e.code, detalle[:200])
            return "reintentar"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            ms = int((time.monotonic() - t0) * 1000)
            self._marcar_offline(str(e))   # timeout / red -> servidor inalcanzable
            # HttpStatus queda NULL: ni siquiera hubo respuesta del servidor.
            self._registrar_envio(ev, "REINTENTAR", request_json=cuerpo_envio,
                                  mensaje_error=f"Fallo de red: {e}", duracion_ms=ms)
            log.warning("Marca %s: fallo de red (%s), se reintenta", ev["id_evento"], e)
            return "sin_conexion"

    def _registrar_envio(self, ev, clasificacion, **kw):
        """Anota el intento en tor.EnviosApi. Si la marca aún no llegó a la BD
        local (BD caída), no hay dónde colgarlo: se omite sin romper el ciclo."""
        if not config.USAR_BD_LOCAL:
            return
        id_marca = ev.get("id_marca_local")
        if id_marca is None:
            id_marca = self.bd_local.id_marca_por_evento(ev.get("id_evento"))
            if id_marca is None:
                return
            ev["id_marca_local"] = id_marca
            self.store.marcar(ev["id"], extra={"id_marca_local": id_marca})
        self.bd_local.registrar_envio_bakelite(id_marca, clasificacion, **kw)

    def _registrar_error(self, origen, mensaje, detalle=None, nivel="ERROR"):
        if config.USAR_BD_LOCAL:
            self.bd_local.registrar_error(origen, mensaje, nivel=nivel, detalle=detalle)

