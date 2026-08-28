# -*- coding: utf-8 -*-
"""Cola SQLite persistente de marcas pendientes de SQL Server y BakeliteApi."""

import datetime
import json
import logging
import os
import sqlite3
import threading
from uuid import uuid4

import config
from almacen_sqlite import ahora_iso, conectar
from rut import formatea_rut

log = logging.getLogger("registros")


def _ahora():
    return datetime.datetime.now().astimezone()


class RegistroStore:
    """Bandeja de salida transaccional compatible con Windows y Linux.

    Conserva la interfaz histórica (`registrar`, `pendientes`, `marcar` y
    `resumen`) para que controlador y sincronizador no dependan del motor usado.
    """

    def __init__(self, ruta=None, ruta_json=None):
        self.ruta = ruta or config.ARCHIVO_SQLITE_LOCAL
        self.ruta_json = ruta_json or config.ARCHIVO_REGISTROS_JSON_LEGACY
        self._lock = threading.RLock()
        self._conexion = conectar(self.ruta)
        self._cerrado = False
        self._migrar_json_legacy()
        resumen = self.resumen()
        log.info(
            "Cola SQLite lista: %d eventos; %d pendientes locales y %d de API.",
            resumen["total"], resumen["pend_local"], resumen["pend_api"],
        )

    @staticmethod
    def _normalizar_evento(evento):
        evento = dict(evento)
        evento["subido_local"] = int(evento.get("subido_local") or 0)
        api = evento.get("subido_api", 0)
        evento["subido_api"] = int(api if api in (-1, 0, 1) else bool(api))
        evento.setdefault("id_marca_local", None)
        evento.setdefault("idMarca", None)
        evento.setdefault("api_error", None)
        return evento

    @staticmethod
    def _desde_fila(fila):
        evento = json.loads(fila["EventoJson"])
        evento["id"] = fila["Id"]
        evento["id_evento"] = fila["IdEvento"]
        evento["subido_local"] = fila["SubidoLocal"]
        evento["subido_api"] = fila["SubidoApi"]
        evento["id_marca_local"] = fila["IdMarcaLocal"]
        evento["idMarca"] = fila["IdMarcaApi"]
        if fila["ApiError"]:
            evento["api_error"] = fila["ApiError"]
        return evento

    def _insertar(self, evento, preservar_id=False):
        evento = self._normalizar_evento(evento)
        id_evento = str(evento.get("id_evento") or evento.get("idEvento") or uuid4().hex)
        evento["id_evento"] = id_evento
        creado = str(evento.get("timestamp") or ahora_iso())
        actualizado = ahora_iso()
        columnas = (
            id_evento,
            json.dumps(evento, ensure_ascii=False, separators=(",", ":")),
            evento["subido_local"],
            evento["subido_api"],
            evento.get("id_marca_local"),
            evento.get("idMarca"),
            evento.get("api_error"),
            creado,
            actualizado,
        )
        id_anterior = evento.get("id") if preservar_id else None
        if id_anterior:
            try:
                cursor = self._conexion.execute(
                    "INSERT INTO ColaEventos "
                    "(Id, IdEvento, EventoJson, SubidoLocal, SubidoApi, "
                    " IdMarcaLocal, IdMarcaApi, ApiError, CreadoEn, ActualizadoEn) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (int(id_anterior), *columnas),
                )
                return cursor.lastrowid
            except sqlite3.IntegrityError:
                existente = self._conexion.execute(
                    "SELECT Id FROM ColaEventos WHERE IdEvento = ?", (id_evento,)
                ).fetchone()
                if existente:
                    return existente["Id"]

        cursor = self._conexion.execute(
            "INSERT OR IGNORE INTO ColaEventos "
            "(IdEvento, EventoJson, SubidoLocal, SubidoApi, IdMarcaLocal, "
            " IdMarcaApi, ApiError, CreadoEn, ActualizadoEn) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            columnas,
        )
        if cursor.rowcount:
            return cursor.lastrowid
        existente = self._conexion.execute(
            "SELECT Id FROM ColaEventos WHERE IdEvento = ?", (id_evento,)
        ).fetchone()
        return existente["Id"] if existente else None

    def _migrar_json_legacy(self):
        """Importa `registros.json` una sola vez y conserva el original."""
        with self._lock:
            migrado = self._conexion.execute(
                "SELECT Valor FROM Metadatos WHERE Clave = 'registros_json_migrado'"
            ).fetchone()
            if migrado or not os.path.isfile(self.ruta_json):
                return

            try:
                with open(self.ruta_json, "r", encoding="utf-8") as archivo:
                    contenido = json.load(archivo)
                registros = contenido.get("registros", [])
                if not isinstance(registros, list):
                    raise ValueError("el campo 'registros' no es una lista")
            except Exception as error:  # noqa: BLE001
                log.critical(
                    "No se migró %s: %s. El archivo quedó intacto.",
                    self.ruta_json, error,
                )
                return

            importados = 0
            omitidos = 0
            try:
                with self._conexion:
                    for evento in registros:
                        if not isinstance(evento, dict):
                            omitidos += 1
                            continue
                        id_evento = evento.get("id_evento") or evento.get("idEvento")
                        ya_existe = bool(id_evento and self._conexion.execute(
                            "SELECT 1 FROM ColaEventos WHERE IdEvento = ?", (str(id_evento),)
                        ).fetchone())
                        self._insertar(evento, preservar_id=True)
                        if not ya_existe:
                            importados += 1
                    self._conexion.execute(
                        "INSERT OR REPLACE INTO Metadatos (Clave, Valor, ActualizadoEn) "
                        "VALUES ('registros_json_migrado', ?, ?)",
                        (str(importados), ahora_iso()),
                    )
            except Exception:  # noqa: BLE001
                log.critical("Falló la migración transaccional del JSON.", exc_info=True)
                return

            sello = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            respaldo = f"{self.ruta_json}.migrado-{sello}.bak"
            try:
                os.replace(self.ruta_json, respaldo)
                if os.name != "nt":
                    os.chmod(respaldo, 0o600)
                log.info(
                    "Migración JSON→SQLite terminada: %d importados, %d omitidos; "
                    "respaldo: %s", importados, omitidos, respaldo,
                )
            except OSError as error:
                log.warning(
                    "Se importó el JSON, pero no se pudo renombrar como respaldo: %s",
                    error,
                )

    def registrar(self, rut, nombre, sentido, codigo, autorizado, ubicacion="", motivo="",
                  id_evento=None, id_marca_local=None):
        """Persiste el evento completo antes de cualquier intento de envío."""
        with self._lock:
            ahora = _ahora()
            id_evento = id_evento or uuid4().hex
            rut_fmt = formatea_rut(rut) or rut
            payload = {
                "idEvento": id_evento,
                "idTerminal": config.ID_TERMINAL,
                "resultado": "AUTORIZADO" if autorizado else "RECHAZADO",
                "rut": rut_fmt,
                "evento": "ENTRADA" if sentido == "E" else "SALIDA",
                "fechaHora": ahora.isoformat(timespec="seconds"),
            }
            if autorizado:
                payload["nombre"] = (nombre or "")[:150]
            else:
                payload["motivoRechazo"] = (motivo or "Acceso no habilitado")[:250]
                nombre_limpio = (nombre or "").strip()
                if (nombre_limpio and nombre_limpio.lower() != "desconocido"
                        and len(nombre_limpio) >= 3):
                    payload["nombre"] = nombre_limpio[:150]

            evento = {
                "id_evento": id_evento,
                "timestamp": ahora.isoformat(timespec="seconds"),
                "fecha": ahora.strftime("%Y-%m-%d"),
                "hora": ahora.strftime("%H:%M:%S"),
                "centro": config.CENTRO,
                "reloj": config.RELOJ,
                "ubicacion": ubicacion,
                "rut": rut,
                "nombre": nombre,
                "sentido": sentido,
                "codigo": codigo,
                "autorizado": bool(autorizado),
                "motivo": motivo,
                "payload": payload,
                "id_marca_local": id_marca_local,
                "idMarca": None,
                "subido_local": 1 if id_marca_local else 0,
                "subido_api": 0,
            }
            with self._conexion:
                rid = self._insertar(evento)
            evento["id"] = rid
            log.info(
                "Evento %s persistido en SQLite (id=%s, local=%s, api=pendiente).",
                id_evento, rid, bool(id_marca_local),
            )
            return dict(evento)

    def pendientes(self):
        with self._lock:
            filas = self._conexion.execute(
                "SELECT * FROM ColaEventos "
                "WHERE SubidoLocal = 0 OR SubidoApi = 0 ORDER BY Id"
            ).fetchall()
            eventos = []
            for fila in filas:
                try:
                    eventos.append(self._desde_fila(fila))
                except (json.JSONDecodeError, TypeError, ValueError) as error:
                    log.critical(
                        "Evento SQLite id=%s tiene JSON inválido y no puede enviarse: %s",
                        fila["Id"], error,
                    )
            return eventos

    def marcar(self, rid, local=None, api=None, extra=None):
        """Actualiza una fila sin reescribir las demás marcas."""
        with self._lock:
            fila = self._conexion.execute(
                "SELECT * FROM ColaEventos WHERE Id = ?", (rid,)
            ).fetchone()
            if not fila:
                log.warning("No existe el evento SQLite id=%s que se intentó actualizar.", rid)
                return False
            evento = self._desde_fila(fila)
            if local is not None:
                evento["subido_local"] = 1 if local else 0
            if api is not None:
                evento["subido_api"] = (
                    1 if api else 0
                ) if isinstance(api, bool) else int(api)
            if extra:
                evento.update(extra)
            actualizado = ahora_iso()
            with self._conexion:
                self._conexion.execute(
                    "UPDATE ColaEventos SET EventoJson = ?, SubidoLocal = ?, "
                    "SubidoApi = ?, IdMarcaLocal = ?, IdMarcaApi = ?, ApiError = ?, "
                    "ActualizadoEn = ? WHERE Id = ?",
                    (
                        json.dumps(evento, ensure_ascii=False, separators=(",", ":")),
                        evento["subido_local"],
                        evento["subido_api"],
                        evento.get("id_marca_local"),
                        evento.get("idMarca"),
                        evento.get("api_error"),
                        actualizado,
                        rid,
                    ),
                )
            log.info(
                "Evento id=%s actualizado (local=%s, api=%s).",
                rid, evento["subido_local"], evento["subido_api"],
            )
            return True

    def resumen(self):
        with self._lock:
            fila = self._conexion.execute(
                "SELECT COUNT(*) AS Total, "
                "SUM(CASE WHEN SubidoLocal = 0 THEN 1 ELSE 0 END) AS PendLocal, "
                "SUM(CASE WHEN SubidoApi = 0 THEN 1 ELSE 0 END) AS PendApi "
                "FROM ColaEventos"
            ).fetchone()
            return {
                "total": int(fila["Total"] or 0),
                "pend_local": int(fila["PendLocal"] or 0),
                "pend_api": int(fila["PendApi"] or 0),
            }

    def cerrar(self):
        with self._lock:
            if self._cerrado:
                return
            self._cerrado = True
            try:
                self._conexion.execute("PRAGMA wal_checkpoint(PASSIVE)")
            finally:
                self._conexion.close()
