# -*- coding: utf-8 -*-
"""Manejador de logging que conserva una bitácora estructurada en SQLite."""

import json
import logging
import re
import sqlite3
import sys
import threading

import config
from almacen_sqlite import ahora_iso, conectar


_SECRETOS = re.compile(
    r"(?i)\b(password|pwd|clave|token|authorization)\s*([=:])\s*"
    r"(?:bearer\s+)?([^;\s,]+)"
)
_CLAVES_SECRETAS = {"password", "pwd", "clave", "token", "authorization"}


def _redactar(valor):
    if not valor:
        return valor
    return _SECRETOS.sub(lambda m: f"{m.group(1)}{m.group(2)}***", str(valor))


def _sanitizar_datos(valor):
    if isinstance(valor, dict):
        return {
            clave: ("***" if str(clave).lower() in _CLAVES_SECRETAS
                    else _sanitizar_datos(contenido))
            for clave, contenido in valor.items()
        }
    if isinstance(valor, (list, tuple)):
        return [_sanitizar_datos(item) for item in valor]
    return valor


class ManejadorBitacoraSQLite(logging.Handler):
    """Guarda cada LogRecord sin depender de SQL Server ni de Internet."""

    def __init__(self, ruta=None, nivel=logging.DEBUG):
        super().__init__(nivel)
        self.ruta = ruta or config.ARCHIVO_SQLITE_LOCAL
        self._conexion = conectar(self.ruta)
        self._db_lock = threading.RLock()
        self._cerrado = False
        self._aplicar_retencion()

    def _aplicar_retencion(self):
        dias = int(getattr(config, "BITACORA_RETENCION_DIAS", 0) or 0)
        if dias <= 0:
            return
        with self._conexion:
            self._conexion.execute(
                "DELETE FROM BitacoraAplicacion "
                "WHERE datetime(FechaHora) < datetime('now', ?)",
                (f"-{dias} days",),
            )

    def emit(self, record):
        if self._cerrado or record.name == "bitacora":
            return
        try:
            excepcion = None
            if record.exc_info:
                excepcion = self.formatException(record.exc_info)
            elif record.exc_text:
                excepcion = record.exc_text

            datos = getattr(record, "datos", None)
            datos_json = None if datos is None else json.dumps(
                _sanitizar_datos(datos), ensure_ascii=False, default=str)
            valores = (
                ahora_iso(),
                record.levelname,
                record.name,
                _redactar(record.getMessage()),
                record.module,
                record.funcName,
                record.lineno,
                record.threadName,
                record.process,
                _redactar(excepcion),
                getattr(record, "flujo", None),
                getattr(record, "origen", None),
                _redactar(datos_json),
            )
            with self._db_lock, self._conexion:
                self._conexion.execute(
                    "INSERT INTO BitacoraAplicacion "
                    "(FechaHora, Nivel, Logger, Mensaje, Modulo, Funcion, Linea, "
                    " Hilo, Proceso, Excepcion, Flujo, Origen, DatosJson) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    valores,
                )
        except (sqlite3.Error, OSError, ValueError) as error:
            # Nunca se usa logging aquí: provocaría recursión si SQLite falla.
            print(f"No se pudo escribir la bitácora SQLite: {error}", file=sys.stderr)

    def formatException(self, exc_info):
        return logging.Formatter().formatException(exc_info)

    def close(self):
        if not self._cerrado:
            self._cerrado = True
            try:
                with self._db_lock:
                    self._conexion.execute("PRAGMA wal_checkpoint(PASSIVE)")
                    self._conexion.close()
            except Exception:  # noqa: BLE001
                pass
        super().close()
