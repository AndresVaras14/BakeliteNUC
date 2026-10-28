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

/* ===========================================================================
   Lo que antes vivía en BakeliteTorniquete (SQL Server local).

   Lo que se sube a Bakelite (marcas, incidentes) sigue en ColaEventos; estas
   tablas son solo lo que el torniquete necesita para operar sin red y sin
   nube: qué lectora/relé es cuál, el estado de conexión en vivo, y una
   ventana corta de las últimas marcas para la pantalla. Nada de esto crece
   sin límite -- ver la poda de UltimasMarcas en basedatos_componentes/marcas.py.
   =========================================================================== */

CREATE TABLE IF NOT EXISTS Terminal (
    IdTerminal INTEGER PRIMARY KEY,
    Nombre TEXT NOT NULL,
    Ubicacion TEXT,
    Activo INTEGER NOT NULL DEFAULT 1,
    NombreFecha TEXT NOT NULL,
    NombreOrigen TEXT NOT NULL DEFAULT 'LOCAL' CHECK (NombreOrigen IN ('LOCAL','API')),
    NombrePor TEXT,
    NombreSincronizado INTEGER NOT NULL DEFAULT 0,
    ArduinoConectado INTEGER,
    ArduinoPuerto TEXT,
    ArduinoEstadoFecha TEXT
);

CREATE TABLE IF NOT EXISTS Lectoras (
    IdTerminal INTEGER NOT NULL,
    Numero INTEGER NOT NULL,
    -- Sin CHECK a propósito: dispositivos.py usa 'X' como valor transitorio
    -- al intercambiar sentidos (ver _set_sentido). La app ya valida 'E'/'S'
    -- antes de escribir, así que no hace falta duplicarlo aquí.
    Sentido TEXT NOT NULL,
    Descripcion TEXT,
    Activa INTEGER NOT NULL DEFAULT 1,
    ConfigFecha TEXT NOT NULL,
    ConfigOrigen TEXT NOT NULL DEFAULT 'LOCAL' CHECK (ConfigOrigen IN ('LOCAL','API')),
    ConfigPor TEXT,
    Sincronizado INTEGER NOT NULL DEFAULT 1,
    Conectada INTEGER,
    UltimoPuerto TEXT,
    UltimaLectura TEXT,
    UltimoError TEXT,
    EstadoFecha TEXT,
    Ancla TEXT,
    PRIMARY KEY (IdTerminal, Numero)
);
CREATE UNIQUE INDEX IF NOT EXISTS UX_Lectoras_Sentido
    ON Lectoras (IdTerminal, Sentido) WHERE Activa = 1;

CREATE TABLE IF NOT EXISTS Reles (
    IdTerminal INTEGER NOT NULL,
    Numero INTEGER NOT NULL,
    -- Sin CHECK por el mismo motivo que en Lectoras (ver arriba).
    Sentido TEXT NOT NULL,
    Comando TEXT NOT NULL,
    Descripcion TEXT,
    Activo INTEGER NOT NULL DEFAULT 1,
    ConfigFecha TEXT NOT NULL,
    ConfigOrigen TEXT NOT NULL DEFAULT 'LOCAL' CHECK (ConfigOrigen IN ('LOCAL','API')),
    ConfigPor TEXT,
    Sincronizado INTEGER NOT NULL DEFAULT 1,
    UltimoDisparo TEXT,
    UltimoError TEXT,
    EstadoFecha TEXT,
    PRIMARY KEY (IdTerminal, Numero)
);
CREATE UNIQUE INDEX IF NOT EXISTS UX_Reles_Sentido
    ON Reles (IdTerminal, Sentido) WHERE Activo = 1;

CREATE TABLE IF NOT EXISTS EstadoServicios (
    Servicio TEXT PRIMARY KEY CHECK (Servicio IN ('BAKELITE','EXTERNA')),
    Descripcion TEXT,
    EnLinea INTEGER NOT NULL DEFAULT 0,
    UltimaConexionOk TEXT,
    UltimaFalla TEXT,
    UltimoError TEXT,
    FechaActualizacion TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS IncidentesConexion (
    IdIncidente INTEGER PRIMARY KEY AUTOINCREMENT,
    IdIncidenteUuid TEXT,
    IdTerminal INTEGER NOT NULL,
    Servicio TEXT NOT NULL CHECK (Servicio IN ('BAKELITE','EXTERNA')),
    FechaDeteccion TEXT NOT NULL,
    FechaRecuperacion TEXT,
    IntentosFallidos INTEGER NOT NULL DEFAULT 1,
    PrimerError TEXT,
    UltimoError TEXT,
    EstadoEnvio TEXT NOT NULL DEFAULT 'PENDIENTE'
        CHECK (EstadoEnvio IN ('PENDIENTE','ENVIADO','FALLIDO','NO_APLICA')),
    IntentosEnvio INTEGER NOT NULL DEFAULT 0,
    UltimoIntentoEnvio TEXT,
    FechaEnvio TEXT,
    HttpStatusEnvio INTEGER,
    RespuestaEnvio TEXT,
    ErrorEnvio TEXT,
    IdRegistroApi INTEGER,
    EstadoApi TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS UX_Incidentes_Uuid
    ON IncidentesConexion (IdTerminal, IdIncidenteUuid) WHERE IdIncidenteUuid IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS UX_Incidentes_Abierto
    ON IncidentesConexion (Servicio) WHERE FechaRecuperacion IS NULL;
CREATE INDEX IF NOT EXISTS IX_Incidentes_Pendientes
    ON IncidentesConexion (IdIncidente)
    WHERE EstadoEnvio = 'PENDIENTE' AND FechaRecuperacion IS NOT NULL;

/* Ventana corta para la pantalla (historial), no el registro permanente --
   ese ya es BakeliteApi. Se poda a un tope fijo en cada inserción. */
CREATE TABLE IF NOT EXISTS UltimasMarcas (
    Id INTEGER PRIMARY KEY AUTOINCREMENT,
    IdEvento TEXT,
    IdTerminal INTEGER,
    FechaHora TEXT NOT NULL,
    Rut TEXT,
    RutFormateado TEXT,
    Nombre TEXT,
    Evento TEXT NOT NULL CHECK (Evento IN ('ENTRADA','SALIDA')),
    Ubicacion TEXT,
    Habilitado INTEGER,
    Motivo TEXT,
    Resultado TEXT,
    PayloadJson TEXT,
    EstadoEnvio TEXT NOT NULL DEFAULT 'PENDIENTE'
        CHECK (EstadoEnvio IN ('NO_APLICA','PENDIENTE','ENVIADA','FALLIDA')),
    Intentos INTEGER NOT NULL DEFAULT 0,
    UltimoError TEXT,
    IdMarcaApi INTEGER,
    EstadoApi TEXT
);
CREATE INDEX IF NOT EXISTS IX_UltimasMarcas_Evento ON UltimasMarcas (IdEvento);
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
