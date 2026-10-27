# -*- coding: utf-8 -*-
"""Conexión multiplataforma y ejecución segura contra SQL Server."""

import datetime
import logging
import struct
import sys
import threading

import config

log = logging.getLogger("basedatos")

try:
    import pyodbc
except ImportError:
    pyodbc = None

ES_WINDOWS = sys.platform == "win32"
if ES_WINDOWS:
    try:
        import pymssql
    except ImportError:
        pymssql = None
else:
    pymssql = None

SQL_TIMESTAMPOFFSET = -155

def _leer_datetimeoffset(valor):
    """Convierte el DATETIMEOFFSET crudo en un datetime con zona horaria."""
    if valor is None:
        return None
    if isinstance(valor, (bytes, bytearray)):
        try:
            a, me, d, h, mi, s, ns, oh, om = struct.unpack("<6hI2h", valor)
            return datetime.datetime(
                a, me, d, h, mi, s, ns // 1000,
                datetime.timezone(datetime.timedelta(hours=oh, minutes=om)))
        except Exception:  # noqa: BLE001
            valor = valor.decode("utf-16-le", "ignore").rstrip("\x00")
    try:
        return datetime.datetime.fromisoformat(str(valor).strip())
    except Exception:  # noqa: BLE001
        return str(valor)


def drivers_disponibles():
    """Drivers ODBC instalados en el equipo."""
    if pyodbc is None:
        return []
    try:
        return list(pyodbc.drivers())
    except Exception:  # noqa: BLE001
        return []


def elegir_driver():
    """Driver a usar. Con SQL_DRIVER = 'AUTO' toma el de Microsoft si está
    instalado y, si no, FreeTDS (paquete tdsodbc de Ubuntu)."""
    if config.SQL_DRIVER and config.SQL_DRIVER.upper() != "AUTO":
        return config.SQL_DRIVER

    instalados = drivers_disponibles()
    for version in (18, 17):
        preferido = f"ODBC Driver {version} for SQL Server"
        if preferido in instalados:
            return preferido
    for d in instalados:
        if "ODBC Driver" in d and "SQL Server" in d:
            return d
    for d in instalados:
        if "FreeTDS" in d or "SQL Server" in d:
            return d
    return "ODBC Driver 18 for SQL Server"   # mensaje de error claro si no está


def cadena_conexion(driver=None):
    """Cadena ODBC construida desde config.py. Las opciones de cifrado del
    driver de Microsoft no existen en FreeTDS, que usa TDS_Version."""
    driver = driver or elegir_driver()
    es_freetds = "freetds" in driver.lower()

    partes = [f"DRIVER={{{driver}}}", f"SERVER={config.SQL_SERVIDOR}"]
    if es_freetds:
        partes += [f"PORT={config.SQL_PUERTO}", "TDS_Version=7.4"]
    partes.append(f"DATABASE={config.SQL_BASE}")

    if config.SQL_TRUSTED:
        partes.append("Trusted_Connection=yes")
    else:
        partes.append(f"UID={config.SQL_USUARIO}")
        partes.append(f"PWD={config.SQL_CLAVE}")

    if not es_freetds:
        partes += [
            f"Encrypt={'yes' if config.SQL_ENCRYPT else 'no'}",
            f"TrustServerCertificate={'yes' if config.SQL_TRUST_CERT else 'no'}",
            f"Connection Timeout={config.SQL_TIMEOUT_CONEXION}",
        ]
    return ";".join(partes) + ";"


class _CursorPymssql:
    """Adapta pymssql al estilo de parámetros ``?`` usado por pyodbc."""

    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, consulta, *parametros):
        consulta = consulta.replace("?", "%s")
        self._cursor.execute(consulta, tuple(parametros))
        return self

    def __getattr__(self, nombre):
        return getattr(self._cursor, nombre)


class ConexionMixin:
    """Ciclo de vida y reconexión de BDLocal."""

    def __init__(self, cadena=None):
        self.cadena = cadena or cadena_conexion()
        self._lock = threading.RLock()
        self._con = None
        self._backend = None
        self.disponible = False
        self.ultimo_error = None
        self.conectar()

    # ================= Conexión =================
    def conectar(self):
        """Abre la conexión. Devuelve True si quedó utilizable."""
        with self._lock:
            self._cerrar_silencioso()
            errores = []

            if pyodbc is not None and drivers_disponibles():
                try:
                    self._con = pyodbc.connect(
                        self.cadena, autocommit=False,
                        timeout=config.SQL_TIMEOUT_CONEXION)
                    self._con.timeout = config.SQL_TIMEOUT_CONSULTA
                    self._con.add_output_converter(
                        SQL_TIMESTAMPOFFSET, _leer_datetimeoffset)
                    self._backend = "pyodbc"
                    return self._conexion_lista()
                except Exception as e:  # noqa: BLE001
                    errores.append(f"ODBC: {e}")
                    self._con = None
                    if ES_WINDOWS and pymssql is not None:
                        log.warning("ODBC no pudo conectar; probando pymssql: %s", e)
                    else:
                        log.warning("ODBC no pudo conectar: %s", e)
            elif pyodbc is None:
                errores.append("pyodbc no está instalado")
            else:
                errores.append("no hay ningún driver ODBC instalado")

            if ES_WINDOWS and pymssql is not None and not config.SQL_TRUSTED:
                try:
                    self._con = pymssql.connect(
                        server=config.SQL_SERVIDOR,
                        user=config.SQL_USUARIO,
                        password=config.SQL_CLAVE,
                        database=config.SQL_BASE,
                        port=str(config.SQL_PUERTO),
                        login_timeout=config.SQL_TIMEOUT_CONEXION,
                        timeout=config.SQL_TIMEOUT_CONSULTA,
                        charset="UTF-8",
                        tds_version="7.4",
                        autocommit=False,
                    )
                    self._backend = "pymssql"
                    return self._conexion_lista()
                except Exception as e:  # noqa: BLE001
                    errores.append(f"pymssql: {e}")
                    self._con = None
            elif ES_WINDOWS and pymssql is None:
                errores.append("pymssql no está instalado")
            elif ES_WINDOWS:
                errores.append("pymssql no admite SQL_TRUSTED")

            self._backend = None
            self.disponible = False
            self.ultimo_error = " | ".join(errores)
            log.error("No se pudo conectar a la BD local: %s", self.ultimo_error)
            return False

    def _conexion_lista(self):
        self.disponible = True
        self.ultimo_error = None
        log.info("Conectado a %s en %s mediante %s",
                 config.SQL_BASE, config.SQL_SERVIDOR, self._backend)
        return True

    def cerrar(self):
        with self._lock:
            self._cerrar_silencioso()
            self.disponible = False

    def _cerrar_silencioso(self):
        if self._con is not None:
            try:
                self._con.close()
            except Exception as e:  # noqa: BLE001
                log.debug("Error cerrando la conexión %s: %s", self._backend, e)
            self._con = None
            self._backend = None

    def _ejecutar(self, fn, descripcion):
        """Corre `fn(cursor)` dentro de una transacción. Reintenta una vez si la
        conexión se cayó. Devuelve (ok, resultado)."""
        for intento in (1, 2):
            with self._lock:
                if self._con is None and not self.conectar():
                    return False, None
                cur = None
                try:
                    cur = self._con.cursor()
                    if self._backend == "pymssql":
                        cur = _CursorPymssql(cur)
                    res = fn(cur)
                    self._con.commit()
                    self.disponible = True
                    return True, res
                except Exception as e:  # noqa: BLE001
                    try:
                        self._con.rollback()
                    except Exception as rollback_error:  # noqa: BLE001
                        log.debug("Rollback falló durante %s: %s",
                                  descripcion, rollback_error)
                    caida = (
                        self._backend == "pyodbc"
                        and pyodbc is not None
                        and isinstance(e, (pyodbc.OperationalError,
                                           pyodbc.InterfaceError))
                    ) or (
                        self._backend == "pymssql"
                        and pymssql is not None
                        and isinstance(e, pymssql.Error)
                    )
                    if caida and intento == 1:
                        log.warning("Conexión perdida en %s, reconectando...", descripcion)
                        self.conectar()
                        continue
                    if caida:
                        self.disponible = False
                    self.ultimo_error = str(e)
                    log.error("Error de BD en %s: %s", descripcion, e)
                    return False, None
                finally:
                    if cur is not None:
                        try:
                            cur.close()
                        except Exception as cierre_error:  # noqa: BLE001
                            log.debug("No se pudo cerrar cursor de %s: %s",
                                      descripcion, cierre_error)
        return False, None

