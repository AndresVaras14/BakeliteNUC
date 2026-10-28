# -*- coding: utf-8 -*-
"""Historial corto de marcas para la pantalla (SQLite).

Ya NO es el registro permanente de accesos -- ese es BakeliteApi (la nube), y
lo que falta subirle vive en ColaEventos (registros.py), que es autosuficiente
y no depende de esto para nada. UltimasMarcas es solo una ventana corta
(TOPE_ULTIMAS_MARCAS filas) para que la pantalla tenga algo que mostrar al
abrir la app y mientras el torniquete está en uso -- se poda sola en cada
inserción, nunca crece.

Los errores (`registrar_error`) ya no tienen una tabla propia: se escriben con
el logging normal de Python, que `ManejadorBitacoraSQLite` (bitacora.py) ya
captura en BitacoraAplicacion con nivel, módulo, mensaje y detalle -- tener
una segunda tabla de errores habría sido duplicar lo que ya existe.
"""

import json
import logging

import config
from .comun import _ahora_iso, _corta, _evento_largo

log = logging.getLogger("basedatos")

# Cuántas marcas se conservan para la pantalla. El historial que se muestra
# (HIST_MEMORIA en interfaz_componentes/estado_operacion.py) es más chico; se
# deja margen porque algunas lecturas quedan "sin respuesta" y no se muestran
# todas igual de seguido.
TOPE_ULTIMAS_MARCAS = 10

_NIVEL_LOG = {"INFO": logging.INFO, "WARN": logging.WARNING,
              "ERROR": logging.ERROR, "CRITICO": logging.CRITICAL}


class MarcasMixin:
    """Historial corto de marcas, para la pantalla."""

    def registrar_marca(self, rut, evento, fecha_hora, rut_formateado=None,
                        ubicacion=None, id_evento=None):
        """Dentro de UltimasMarcas: abre la fila apenas se lee la cédula, antes
        de saber si está habilitada -- `registrar_consulta_externa` la
        completa después. `evento` es 'ENTRADA'/'SALIDA' (acepta 'E'/'S').

        Devuelve {'id_marca', 'id_evento'} o None si la BD no está disponible.
        El IdEvento es el UUID del contrato: se crea UNA sola vez aquí y no se
        vuelve a generar en los reintentos.
        """
        from uuid import uuid4
        id_evento = id_evento or uuid4().hex

        def _fn(cur):
            cur.execute(
                "INSERT INTO UltimasMarcas "
                "(IdEvento, IdTerminal, Rut, RutFormateado, Evento, FechaHora, "
                " Ubicacion, EstadoEnvio) "
                "VALUES (?,?,?,?,?,?,?,'PENDIENTE')",
                (id_evento, config.ID_TERMINAL, rut, rut_formateado,
                 _evento_largo(evento), str(fecha_hora), _corta(ubicacion, 200)))
            id_marca = cur.lastrowid
            self._podar_ultimas_marcas(cur)
            return id_marca

        ok, id_marca = self._ejecutar(_fn, "registrar_marca")
        if not ok:
            return None
        return {"id_marca": id_marca, "id_evento": id_evento}

    def _podar_ultimas_marcas(self, cur):
        cur.execute(
            "DELETE FROM UltimasMarcas WHERE Id NOT IN "
            "(SELECT Id FROM UltimasMarcas ORDER BY Id DESC LIMIT ?)",
            (TOPE_ULTIMAS_MARCAS,))

    # ================= Paso 2: consulta a la API externa =================
    def registrar_consulta_externa(self, rut_consultado, id_marca=None,
                                   habilitado=None, nombre=None, motivo=None,
                                   rut_respuesta=None, http_status=None,
                                   respuesta_json=None, exito=False,
                                   mensaje_error=None, duracion_ms=None, url=None):
        """Copia el resultado de la API externa sobre la marca (Habilitado /
        Nombre / Motivo / Resultado). habilitado: True habilitado · False
        rechazado · None sin respuesta. Devuelve True si se aplicó."""
        if id_marca is None:
            return None
        hab = None if habilitado is None else (1 if habilitado else 0)
        if hab == 1:
            resultado = "AUTORIZADO"
        elif hab == 0:
            resultado = "RECHAZADO"
        else:
            resultado = "SIN_RESPUESTA"

        # "Desconocido" es el relleno que usa la app cuando la API externa no
        # identifica a la persona: no es un nombre de verdad.
        nombre = (nombre or "").strip()
        if nombre.lower() == "desconocido" or len(nombre) < 3:
            nombre = None
        nombre = _corta(nombre, 150)
        motivo = _corta((motivo or "").strip() or None, 250)

        def _fn(cur):
            if hab is None:
                # Sin respuesta: la marca no entra a la cola de envío.
                cur.execute(
                    "UPDATE UltimasMarcas "
                    "SET Habilitado = ?, Resultado = ?, EstadoEnvio = 'NO_APLICA' "
                    "WHERE Id = ?", (hab, resultado, id_marca))
            else:
                cur.execute(
                    "UPDATE UltimasMarcas "
                    "SET Habilitado = ?, Nombre = COALESCE(?, Nombre), Motivo = ? , "
                    "    Resultado = ? WHERE Id = ?",
                    (hab, nombre, motivo, resultado, id_marca))
            return cur.rowcount > 0

        ok, res = self._ejecutar(_fn, "registrar_consulta_externa")
        return bool(ok and res)

    def guardar_payload(self, id_marca, payload):
        """Deja escrito el JSON exacto que se enviará a BakeliteApi. No hace
        falta para los reintentos (esos ya leen de ColaEventos), pero queda
        guardado igual por si hace falta revisar una marca puntual."""
        if id_marca is None:
            return False
        cuerpo = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)

        def _fn(cur):
            cur.execute("UPDATE UltimasMarcas SET PayloadJson = ? WHERE Id = ?",
                        (cuerpo, id_marca))
            return cur.rowcount > 0

        ok, res = self._ejecutar(_fn, "guardar_payload")
        return bool(ok and res)

    # ================= Paso 3: envío a BakeliteApi =================
    def registrar_envio_bakelite(self, id_marca, clasificacion, http_status=None,
                                 request_json=None, respuesta_json=None,
                                 estado_api=None, id_marca_api=None,
                                 mensaje_error=None, duracion_ms=None, url=None):
        """Refleja en UltimasMarcas si el envío a Bakelite salió bien o no, para
        que la pantalla sepa si una marca sigue pendiente.

        clasificacion: 'OK'         -> ENVIADA
                       'PERMANENTE' -> FALLIDA (no se reintenta)
                       'REINTENTAR' -> sigue PENDIENTE (red, 429, 5xx)
        Si la fila ya salió del buffer corto (se podó), no hay dónde anotarlo:
        no es un error, la marca igual se subió bien -- esto es solo para la
        pantalla, el envío real lo decide ColaEventos.
        """
        if id_marca is None:
            return None
        estado = {"OK": "ENVIADA", "PERMANENTE": "FALLIDA"}.get(clasificacion, "PENDIENTE")

        def _fn(cur):
            cur.execute(
                "UPDATE UltimasMarcas "
                "SET Intentos = Intentos + 1, EstadoEnvio = ?, "
                "    IdMarcaApi = COALESCE(?, IdMarcaApi), EstadoApi = COALESCE(?, EstadoApi), "
                "    UltimoError = ? WHERE Id = ?",
                (estado, id_marca_api, estado_api,
                 None if estado == "ENVIADA" else _corta(mensaje_error, 500), id_marca))
            cur.execute("SELECT Intentos FROM UltimasMarcas WHERE Id = ?", (id_marca,))
            fila = cur.fetchone()
            return int(fila[0]) if fila else None

        ok, res = self._ejecutar(_fn, "registrar_envio_bakelite")
        return res if ok else None

    def recuperar_marca(self, ev):
        """Rehace en UltimasMarcas un evento que quedó solo en ColaEventos
        porque la BD local no tenía todavía la fila (arranque en curso, etc).
        Idempotente por IdEvento. Devuelve el id de la fila o None."""
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
            motivo=ev.get("motivo"), exito=True)
        if payload:
            self.guardar_payload(id_marca, payload)

        estado_api = ev.get("subido_api")
        if estado_api == 1:
            self.registrar_envio_bakelite(
                id_marca, "OK", http_status=200,
                estado_api="RECONSTRUIDO", id_marca_api=ev.get("idMarca"))
        elif estado_api == -1:
            self.registrar_envio_bakelite(
                id_marca, "PERMANENTE",
                mensaje_error=ev.get("api_error") or "Rechazada por la API")
        return id_marca

    # ================= Consultas =================
    def marcas_pendientes(self, tope=100):
        """Sin uso real: ColaEventos (self.store.pendientes()) es la fuente de
        lo que falta subir. Se conserva por compatibilidad de la interfaz."""
        return []

    def ultimas_marcas(self, tope=50):
        """Para precargar la pantalla al abrir la app (ver main.py)."""
        def _fn(cur):
            cur.execute(
                "SELECT Id, FechaHora, Rut, Nombre, Evento, Habilitado, "
                "       Motivo, Resultado, EstadoEnvio "
                "FROM UltimasMarcas ORDER BY Id DESC LIMIT ?", (tope,))
            return [{"id_marca": r[0], "fecha": r[1], "rut": r[2], "nombre": r[3],
                     "evento": r[4], "habilitado": r[5], "motivo": r[6],
                     "resultado": r[7], "estado_envio": r[8]} for r in cur.fetchall()]

        ok, res = self._ejecutar(_fn, "ultimas_marcas")
        return res if ok else []

    def id_marca_por_evento(self, id_evento):
        def _fn(cur):
            cur.execute("SELECT Id FROM UltimasMarcas WHERE IdTerminal = ? AND IdEvento = ?",
                        (config.ID_TERMINAL, id_evento))
            fila = cur.fetchone()
            return int(fila[0]) if fila else None

        ok, res = self._ejecutar(_fn, "id_marca_por_evento")
        return res if ok else None

    def resumen(self):
        """Estadística sobre la ventana corta (TOPE_ULTIMAS_MARCAS filas), no
        un total histórico -- ese vive en Bakelite. Sin uso real en la app;
        se conserva por compatibilidad."""
        def _fn(cur):
            cur.execute(
                "SELECT COUNT(*), "
                "       SUM(CASE WHEN EstadoEnvio = 'PENDIENTE' THEN 1 ELSE 0 END), "
                "       SUM(CASE WHEN EstadoEnvio = 'ENVIADA'   THEN 1 ELSE 0 END), "
                "       SUM(CASE WHEN EstadoEnvio = 'FALLIDA'   THEN 1 ELSE 0 END) "
                "FROM UltimasMarcas")
            r = cur.fetchone()
            return {"total": r[0] or 0, "pendientes": r[1] or 0, "enviadas": r[2] or 0,
                    "fallidas": r[3] or 0, "ultimo_envio": None}

        ok, res = self._ejecutar(_fn, "resumen")
        return res if ok else {"total": 0, "pendientes": 0, "enviadas": 0,
                               "fallidas": 0, "ultimo_envio": None}

    # ================= Errores =================
    def registrar_error(self, origen, mensaje, nivel="ERROR", detalle=None, id_marca=None):
        """Ya no tiene tabla propia: pasa por el logging normal, que la
        bitácora (BitacoraAplicacion) ya captura con nivel, módulo y detalle."""
        logging.getLogger(f"bd.{origen}").log(
            _NIVEL_LOG.get(nivel, logging.ERROR), "%s%s",
            mensaje, f" | {detalle}" if detalle else "")
        return True
