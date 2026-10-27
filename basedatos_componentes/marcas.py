# -*- coding: utf-8 -*-
import json
import logging
from uuid import uuid4

import config
from .comun import _ahora_iso, _corta, _evento_largo, _texto_fecha

log = logging.getLogger("basedatos")

class MarcasMixin:
    """Marcas, consultas externas, envíos, consultas y errores."""

    def registrar_marca(self, rut, evento, fecha_hora, rut_formateado=None,
                        ubicacion=None, id_evento=None):
        """Guarda la marca apenas se lee la cédula, antes de saber si está
        habilitada. `evento` es 'ENTRADA'/'SALIDA' (acepta 'E'/'S').

        Devuelve {'id_marca', 'id_evento'} o None si la BD no está disponible.
        El IdEvento es el UUID del contrato: se crea UNA sola vez aquí y no se
        vuelve a generar en los reintentos.
        """
        id_evento = id_evento or uuid4().hex

        def _fn(cur):
            cur.execute("SELECT IdTrabajador FROM dbo.Trabajadores WHERE Rut = ?", rut)
            fila = cur.fetchone()
            id_trabajador = int(fila[0]) if fila else None

            cur.execute("SELECT TOP (1) IdVersion FROM dbo.Versiones WHERE Activo = 1")
            fila = cur.fetchone()
            id_version = int(fila[0]) if fila else None

            cur.execute(
                "INSERT INTO dbo.Marcas "
                "(IdEvento, IdTerminal, IdTrabajador, Rut, RutFormateado, Evento, "
                " FechaHora, Ubicacion, EstadoEnvio, IdVersion) "
                "OUTPUT INSERTED.IdMarca "
                "VALUES (?,?,?,?,?,?,?,?,'PENDIENTE',?)",
                id_evento, config.ID_TERMINAL, id_trabajador, rut, rut_formateado,
                _evento_largo(evento), fecha_hora, _corta(ubicacion, 200), id_version)
            return int(cur.fetchone()[0])

        ok, id_marca = self._ejecutar(_fn, "registrar_marca")
        if not ok:
            return None
        log.debug("Marca %s creada en BD local (IdMarca=%s)", id_evento, id_marca)
        return {"id_marca": id_marca, "id_evento": id_evento}

    # ================= Paso 2: consulta a la API externa =================
    def registrar_consulta_externa(self, rut_consultado, id_marca=None,
                                   habilitado=None, nombre=None, motivo=None,
                                   rut_respuesta=None, http_status=None,
                                   respuesta_json=None, exito=False,
                                   mensaje_error=None, duracion_ms=None, url=None):
        """Guarda la pregunta y la respuesta de la API externa, y copia el
        resultado sobre la marca (Habilitado / Nombre / Motivo / Resultado).

        habilitado: True/1 habilitado · False/0 rechazado · None sin respuesta.
        Devuelve IdConsulta o None.
        """
        hab = None if habilitado is None else (1 if habilitado else 0)
        if hab == 1:
            resultado = "AUTORIZADO"
        elif hab == 0:
            resultado = "RECHAZADO"
        else:
            resultado = "SIN_RESPUESTA"

        # "Desconocido" es el relleno que usa la app cuando la API externa no
        # identifica a la persona: no es un nombre y no debe crear trabajador.
        nombre = (nombre or "").strip()
        if nombre.lower() == "desconocido" or len(nombre) < 3:
            nombre = None
        nombre = _corta(nombre, 150)
        motivo = _corta((motivo or "").strip() or None, 250)

        def _fn(cur):
            cur.execute(
                "INSERT INTO dbo.ConsultasApiExterna "
                "(IdMarca, RutConsultado, Url, HttpStatus, RutRespuesta, Habilitado, "
                " Nombre, Motivo, RespuestaJson, Exito, MensajeError, DuracionMs) "
                "OUTPUT INSERTED.IdConsulta VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                id_marca, rut_consultado, _corta(url, 300), http_status,
                rut_respuesta, hab, nombre, motivo, respuesta_json,
                1 if exito else 0, _corta(mensaje_error, 1000), duracion_ms)
            id_consulta = int(cur.fetchone()[0])

            if id_marca is not None:
                # Sin respuesta de la API externa no hay nada que mandarle a
                # Bakelite: la marca queda como NO_APLICA y no entra a la cola.
                cur.execute(
                    "UPDATE dbo.Marcas "
                    "SET Habilitado = ?, Nombre = COALESCE(?, Nombre), Motivo = ?, "
                    "    Resultado = ?, FechaConsulta = SYSDATETIME(), "
                    "    EstadoEnvio = CASE WHEN ? IS NULL THEN 'NO_APLICA' ELSE EstadoEnvio END "
                    "WHERE IdMarca = ?",
                    hab, nombre, motivo, resultado, hab, id_marca)

            # El trabajador se conoce cuando la API externa devuelve su nombre.
            if nombre and rut_consultado:
                cur.execute("SELECT IdTrabajador FROM dbo.Trabajadores WHERE Rut = ?",
                            rut_consultado)
                fila = cur.fetchone()
                if fila:
                    id_trabajador = int(fila[0])
                    cur.execute(
                        "UPDATE dbo.Trabajadores "
                        "SET Nombre = ?, Habilitado = ?, FechaUltimaMarca = SYSDATETIME() "
                        "WHERE IdTrabajador = ?", nombre, hab, id_trabajador)
                else:
                    cur.execute(
                        "INSERT INTO dbo.Trabajadores "
                        "(Rut, RutFormateado, Nombre, Habilitado, FechaUltimaMarca) "
                        "OUTPUT INSERTED.IdTrabajador VALUES (?,?,?,?, SYSDATETIME())",
                        rut_consultado, rut_respuesta, nombre, hab)
                    id_trabajador = int(cur.fetchone()[0])

                if id_marca is not None:
                    cur.execute("UPDATE dbo.Marcas SET IdTrabajador = ? WHERE IdMarca = ?",
                                id_trabajador, id_marca)
            return id_consulta

        ok, res = self._ejecutar(_fn, "registrar_consulta_externa")
        return res if ok else None

    def guardar_payload(self, id_marca, payload):
        """Deja escrito el JSON exacto que se enviará a BakeliteApi, antes del
        primer intento. En los reintentos se relee de aquí, no se reconstruye."""
        cuerpo = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)

        def _fn(cur):
            cur.execute("UPDATE dbo.Marcas SET PayloadJson = ? WHERE IdMarca = ?",
                        cuerpo, id_marca)
            return cur.rowcount > 0

        ok, res = self._ejecutar(_fn, "guardar_payload")
        return bool(ok and res)

    # ================= Paso 3: envío a BakeliteApi =================
    def registrar_envio_bakelite(self, id_marca, clasificacion, http_status=None,
                                 request_json=None, respuesta_json=None,
                                 estado_api=None, id_marca_api=None,
                                 mensaje_error=None, duracion_ms=None, url=None):
        """Anota el intento contra BakeliteApi y mueve el estado de la marca.

        No guarda una fila por intento: mantiene UNA fila por marca en
        dbo.EnviosBakelite y la va actualizando (primer intento, último,
        cuántos hubo, último error) hasta que uno sale bien, y ahí deja además
        los datos del envío exitoso.

        clasificacion: 'OK'         -> ENVIADA (HTTP 201 REGISTRADO / 200 DUPLICADO)
                       'PERMANENTE' -> FALLIDA (HTTP 400, no se reintenta)
                       'REINTENTAR' -> sigue PENDIENTE (red, 429, 5xx)
        """
        estado = {"OK": "ENVIADA", "PERMANENTE": "FALLIDA"}.get(clasificacion, "PENDIENTE")
        exito = 1 if clasificacion == "OK" else 0

        def _fn(cur):
            cur.execute(
                "UPDATE dbo.EnviosBakelite "
                "SET Intentos = Intentos + 1, UltimoIntento = SYSDATETIME(), "
                "    PrimerIntento = COALESCE(PrimerIntento, SYSDATETIME()), "
                "    RequestJson = COALESCE(?, RequestJson), Url = COALESCE(?, Url), "
                "    UltimoHttpStatus = ?, UltimaRespuesta = ?, UltimoError = ?, "
                "    UltimaDuracionMs = ?, "
                "    Exito = CASE WHEN ? = 1 THEN 1 ELSE Exito END, "
                "    FechaExito = CASE WHEN ? = 1 THEN SYSDATETIME() ELSE FechaExito END, "
                "    HttpStatusExito = CASE WHEN ? = 1 THEN ? ELSE HttpStatusExito END, "
                "    EstadoApi = COALESCE(?, EstadoApi), "
                "    IdMarcaApi = COALESCE(?, IdMarcaApi) "
                "WHERE IdMarca = ?",
                request_json, _corta(url or config.ENDPOINT_REGISTRAR_EVENTO, 300), http_status,
                respuesta_json, _corta(mensaje_error, 1000), duracion_ms,
                exito, exito, exito, http_status, estado_api, id_marca_api, id_marca)

            if cur.rowcount == 0:
                cur.execute(
                    "INSERT INTO dbo.EnviosBakelite "
                    "(IdMarca, Url, RequestJson, Intentos, PrimerIntento, UltimoIntento, "
                    " UltimoHttpStatus, UltimaRespuesta, UltimoError, UltimaDuracionMs, "
                    " Exito, FechaExito, HttpStatusExito, EstadoApi, IdMarcaApi) "
                    "VALUES (?,?,?,1, SYSDATETIME(), SYSDATETIME(), ?,?,?,?,?, "
                    "        CASE WHEN ? = 1 THEN SYSDATETIME() END, "
                    "        CASE WHEN ? = 1 THEN ? END, ?, ?)",
                    id_marca, _corta(url or config.ENDPOINT_REGISTRAR_EVENTO, 300), request_json,
                    http_status, respuesta_json, _corta(mensaje_error, 1000), duracion_ms,
                    exito, exito, exito, http_status, estado_api, id_marca_api)

            cur.execute(
                "UPDATE dbo.Marcas "
                "SET Intentos = Intentos + 1, UltimoIntento = SYSDATETIME(), EstadoEnvio = ?, "
                "    FechaEnvio = CASE WHEN ? = 'ENVIADA' THEN SYSDATETIME() ELSE FechaEnvio END, "
                "    IdMarcaApi = COALESCE(?, IdMarcaApi), "
                "    EstadoApi = COALESCE(?, EstadoApi), "
                "    UltimoError = CASE WHEN ? = 'ENVIADA' THEN NULL ELSE ? END "
                "WHERE IdMarca = ?",
                estado, estado, id_marca_api, estado_api,
                estado, _corta(mensaje_error, 500), id_marca)

            cur.execute("SELECT Intentos FROM dbo.EnviosBakelite WHERE IdMarca = ?", id_marca)
            fila = cur.fetchone()
            return int(fila[0]) if fila else None

        ok, res = self._ejecutar(_fn, "registrar_envio_bakelite")
        return res if ok else None
    def recuperar_marca(self, ev):
        """Rehace en la BD una marca que quedó solo en la cola SQLite porque la BD
        estaba caída cuando se pasó la cédula. Idempotente por IdEvento.

        Respeta lo que ya pasó: si la marca alcanzó a entregarse a Bakelite
        mientras la BD no estaba, se reconstruye directamente como ENVIADA, no
        como pendiente. Devuelve el IdMarca o None.
        """
        id_evento = ev.get("id_evento")
        existente = self.id_marca_por_evento(id_evento)
        if existente is not None:
            return existente

        payload = ev.get("payload") or {}
        creada = self.registrar_marca(
            rut=ev.get("rut"), evento=ev.get("sentido"),
            fecha_hora=ev.get("timestamp"), rut_formateado=payload.get("rut"),
            ubicacion=ev.get("ubicacion"), id_evento=id_evento)
        if creada is None:
            return None

        id_marca = creada["id_marca"]
        self.registrar_consulta_externa(
            rut_consultado=ev.get("rut"), id_marca=id_marca,
            habilitado=bool(ev.get("autorizado")), nombre=ev.get("nombre"),
            motivo=ev.get("motivo"), exito=True,
            mensaje_error="Reconstruida desde la cola local (BD caída al leer)")
        if payload:
            self.guardar_payload(id_marca, payload)

        estado_api = ev.get("subido_api")
        if estado_api == 1:
            # Ya se entregó a Bakelite mientras la BD estaba caída: se refleja
            # el envío para que la marca no quede pendiente para siempre.
            self.registrar_envio_bakelite(
                id_marca, "OK", http_status=200,
                request_json=json.dumps(payload, ensure_ascii=False),
                estado_api="RECONSTRUIDO", id_marca_api=ev.get("idMarca"),
                mensaje_error=None)
        elif estado_api == -1:
            self.registrar_envio_bakelite(
                id_marca, "PERMANENTE",
                request_json=json.dumps(payload, ensure_ascii=False),
                mensaje_error=ev.get("api_error") or "Rechazada por la API")
        return id_marca

    # ================= Consultas =================
    def marcas_pendientes(self, tope=100):
        """Marcas que todavía no llegaron a BakeliteApi. Cada una trae su
        payload textual: se reenvía tal cual, con el mismo IdEvento."""
        def _fn(cur):
            cur.execute(
                "SELECT TOP (?) IdMarca, IdEvento, PayloadJson, Intentos, UltimoError "
                "FROM dbo.Marcas "
                "WHERE EstadoEnvio = 'PENDIENTE' AND PayloadJson IS NOT NULL "
                "ORDER BY IdMarca", tope)
            return [{"id_marca": r[0], "id_evento": r[1],
                     "payload": json.loads(r[2]) if r[2] else None,
                     "payload_json": r[2], "intentos": r[3], "ultimo_error": r[4]}
                    for r in cur.fetchall()]

        ok, res = self._ejecutar(_fn, "marcas_pendientes")
        return res if ok else []

    def ultimas_marcas(self, tope=50):
        def _fn(cur):
            cur.execute(
                "SELECT TOP (?) IdMarca, FechaHora, Rut, Nombre, Evento, Habilitado, "
                "       Motivo, Resultado, EstadoEnvio "
                "FROM dbo.Marcas ORDER BY IdMarca DESC", tope)
            return [{"id_marca": r[0], "fecha": r[1], "rut": r[2], "nombre": r[3],
                     "evento": r[4], "habilitado": r[5], "motivo": r[6],
                     "resultado": r[7], "estado_envio": r[8]} for r in cur.fetchall()]

        ok, res = self._ejecutar(_fn, "ultimas_marcas")
        return res if ok else []

    def id_marca_por_evento(self, id_evento):
        def _fn(cur):
            cur.execute("SELECT IdMarca FROM dbo.Marcas WHERE IdTerminal = ? AND IdEvento = ?",
                        config.ID_TERMINAL, id_evento)
            fila = cur.fetchone()
            return int(fila[0]) if fila else None

        ok, res = self._ejecutar(_fn, "id_marca_por_evento")
        return res if ok else None

    def resumen(self):
        def _fn(cur):
            cur.execute(
                "SELECT COUNT(*), "
                "       SUM(CASE WHEN EstadoEnvio = 'PENDIENTE' THEN 1 ELSE 0 END), "
                "       SUM(CASE WHEN EstadoEnvio = 'ENVIADA'   THEN 1 ELSE 0 END), "
                "       SUM(CASE WHEN EstadoEnvio = 'FALLIDA'   THEN 1 ELSE 0 END), "
                "       MAX(FechaEnvio) FROM dbo.Marcas")
            r = cur.fetchone()
            return {"total": r[0] or 0, "pendientes": r[1] or 0, "enviadas": r[2] or 0,
                    "fallidas": r[3] or 0, "ultimo_envio": r[4]}

        ok, res = self._ejecutar(_fn, "resumen")
        return res if ok else {"total": 0, "pendientes": 0, "enviadas": 0,
                               "fallidas": 0, "ultimo_envio": None}

    # ================= Errores =================
    def registrar_error(self, origen, mensaje, nivel="ERROR", detalle=None, id_marca=None):
        def _fn(cur):
            cur.execute(
                "INSERT INTO dbo.Errores (IdTerminal, Origen, Nivel, Mensaje, Detalle, IdMarca) "
                "VALUES (?,?,?,?,?,?)",
                config.ID_TERMINAL, _corta(origen, 60), nivel,
                _corta(mensaje, 1000), detalle, id_marca)
            return True

        ok, _ = self._ejecutar(_fn, "registrar_error")
        return ok

