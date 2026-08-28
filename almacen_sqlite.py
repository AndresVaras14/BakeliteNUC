# -*- coding: utf-8 -*-
"""Base SQLite local compartida por la cola y la bitácora de la aplicación."""

import datetime
import os
import sqlite3
import sys

import config


ESQUEMA = """
CREATE TABLE IF NOT EXISTS Metadatos (
    Clave TEXT PRIMARY KEY,
    Valor TEXT NOT NULL,
    ActualizadoEn TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ColaEventos (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    IdEvento TEXT NOT NULL UNIQUE,
    EventoJson TEXT NOT NULL,
    SubidoLocal INTEGER NOT NULL DEFAULT 0 CHECK (SubidoLocal IN (0, 1)),
    SubidoApi INTEGER NOT NULL DEFAULT 0 CHECK (SubidoApi IN (-1, 0, 1)),
    IdMarcaLocal INTEGER NULL,
    IdMarcaApi INTEGER NULL,
    ApiError TEXT NULL,
    CreadoEn TEXT NOT NULL,
    ActualizadoEn TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS IX_ColaEventos_PendienteLocal
    ON ColaEventos (SubidoLocal, Id);
CREATE INDEX IF NOT EXISTS IX_ColaEventos_PendienteApi
    ON ColaEventos (SubidoApi, Id);

CREATE TABLE IF NOT EXISTS BitacoraAplicacion (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    FechaHora TEXT NOT NULL,
    Nivel TEXT NOT NULL,
    Logger TEXT NOT NULL,
    Mensaje TEXT NOT NULL,
    Modulo TEXT NULL,
    Funcion TEXT NULL,
    Linea INTEGER NULL,
    Hilo TEXT NULL,
    Proceso INTEGER NULL,
    Excepcion TEXT NULL,
    Flujo TEXT NULL,
    Origen TEXT NULL,
    DatosJson TEXT NULL
);

CREATE INDEX IF NOT EXISTS IX_BitacoraAplicacion_FechaHora
    ON BitacoraAplicacion (FechaHora DESC);
CREATE INDEX IF NOT EXISTS IX_BitacoraAplicacion_NivelFecha
    ON BitacoraAplicacion (Nivel, FechaHora DESC);
CREATE INDEX IF NOT EXISTS IX_BitacoraAplicacion_LoggerFecha
    ON BitacoraAplicacion (Logger, FechaHora DESC);
"""


def ahora_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec="milliseconds")


def _abrir(ruta):
    carpeta = os.path.dirname(os.path.abspath(ruta))
    os.makedirs(carpeta, exist_ok=True)
    conexion = sqlite3.connect(
        ruta,
        timeout=config.SQLITE_BUSY_TIMEOUT_MS / 1000,
        check_same_thread=False,
    )
    if os.name != "nt":
        os.chmod(ruta, 0o600)
    try:
        conexion.row_factory = sqlite3.Row
        conexion.execute(f"PRAGMA busy_timeout = {int(config.SQLITE_BUSY_TIMEOUT_MS)}")
        conexion.execute("PRAGMA journal_mode = WAL")
        conexion.execute("PRAGMA synchronous = FULL")
        conexion.execute("PRAGMA foreign_keys = ON")
        conexion.executescript(ESQUEMA)
        return conexion
    except Exception:
        # Especialmente importante en Windows: un handle abierto impediría
        # mover a cuarentena una base dañada.
        conexion.close()
        raise


def _es_corrupcion(error):
    texto = str(error).lower()
    return any(marca in texto for marca in (
        "database disk image is malformed",
        "file is not a database",
        "database corruption",
        "malformed database schema",
    ))


def _cuarentenar(ruta, error):
    sello = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    destino_base = f"{ruta}.corrupto-{sello}"
    movidos = []
    for sufijo in ("", "-wal", "-shm"):
        origen = ruta + sufijo
        if not os.path.exists(origen):
            continue
        destino = destino_base + sufijo
        os.replace(origen, destino)
        if os.name != "nt":
            os.chmod(destino, 0o600)
        movidos.append(destino)
    print(
        f"CRITICO: SQLite local estaba corrupto ({error}). "
        f"Se aisló en: {', '.join(movidos)}",
        file=sys.stderr,
    )


def conectar(ruta=None):
    """Abre la base local y crea el esquema de forma idempotente.

    Si SQLite confirma corrupción real, conserva los archivos dañados con una
    marca de tiempo y crea una base nueva para que el torniquete pueda iniciar.
    Errores transitorios (bloqueo, permisos o disco lleno) nunca se confunden
    con corrupción y se propagan sin mover datos.
    """
    ruta = ruta or config.ARCHIVO_SQLITE_LOCAL
    try:
        conexion = _abrir(ruta)
        estado = conexion.execute("PRAGMA quick_check").fetchone()[0]
        if estado != "ok":
            conexion.close()
            raise sqlite3.DatabaseError(f"database corruption: {estado}")
        return conexion
    except sqlite3.DatabaseError as error:
        if not _es_corrupcion(error):
            raise
        _cuarentenar(ruta, error)
        return _abrir(ruta)
