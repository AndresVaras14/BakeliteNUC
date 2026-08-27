# -*- coding: utf-8 -*-
"""
BD local del torniquete: SQL Server (BakeliteTorniquete, esquema dbo) vía pyodbc.

Refleja el flujo real de un acceso:

  1. registrar_marca()            Se pasó la cédula. Guarda rut, evento y
                                  fecha/hora. Devuelve IdMarca + IdEvento.
  2. registrar_consulta_externa() Se le preguntó el RUT a la API externa y
                                  respondió (rut, habilitado 1/0, nombre,
                                  motivo). Guarda la consulta completa y copia
                                  el resultado sobre la marca.
  3. registrar_envio_bakelite()   Se intentó subir la marca a BakeliteApi.
                                  Guarda el intento y deja la marca en
                                  ENVIADA / PENDIENTE / FALLIDA.

Todo el SQL vive aquí y va parametrizado con `?` (nunca concatenado). No se usan
procedimientos almacenados.

Si la BD no está disponible ningún método revienta la app: devuelven None/False,
lo dejan pendiente en la cola JSON y se reintenta al reconectar.
"""

import json
import struct
import logging
import datetime
import threading
from uuid import uuid4

import config

log = logging.getLogger("basedatos")

try:
    import pyodbc
except ImportError:  # el equipo aún no tiene el driver instalado
    pyodbc = None


# SQL_SS_TIMESTAMPOFFSET: el tipo DATETIMEOFFSET. Ni FreeTDS ni pyodbc lo
# decodifican solos, así que se registra un conversor para leerlo.
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


class BDLocal:
    """BD local en SQL Server. Reconecta sola y nunca propaga excepciones."""

    def __init__(self, cadena=None):
        self.cadena = cadena or cadena_conexion()
        self._lock = threading.RLock()
        self._con = None
        self.disponible = False
        self.ultimo_error = None
        self.conectar()

    # ================= Conexión =================
    def conectar(self):
        """Abre la conexión. Devuelve True si quedó utilizable."""
        if pyodbc is None:
            self.ultimo_error = ("pyodbc no está instalado "
                                 "(sudo apt install python3-pyodbc)")
            log.error("BD local no disponible: %s", self.ultimo_error)
            return False
        if not drivers_disponibles():
            self.ultimo_error = ("no hay ningún driver ODBC instalado "
                                 "(sudo apt install unixodbc tdsodbc)")
            log.error("BD local no disponible: %s", self.ultimo_error)
            return False
        with self._lock:
            self._cerrar_silencioso()
            try:
                self._con = pyodbc.connect(self.cadena, autocommit=False,
                                           timeout=config.SQL_TIMEOUT_CONEXION)
                self._con.timeout = config.SQL_TIMEOUT_CONSULTA
                self._con.add_output_converter(SQL_TIMESTAMPOFFSET, _leer_datetimeoffset)
                self.disponible = True
                self.ultimo_error = None
                log.info("Conectado a %s en %s", config.SQL_BASE, config.SQL_SERVIDOR)
                return True
            except Exception as e:  # noqa: BLE001
                self._con = None
                self.disponible = False
                self.ultimo_error = str(e)
                log.error("No se pudo conectar a la BD local: %s", e)
                return False

    def cerrar(self):
        with self._lock:
            self._cerrar_silencioso()
            self.disponible = False

    def _cerrar_silencioso(self):
        if self._con is not None:
            try:
                self._con.close()
            except Exception:  # noqa: BLE001
                pass
            self._con = None

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
                    res = fn(cur)
                    self._con.commit()
                    self.disponible = True
                    return True, res
                except Exception as e:  # noqa: BLE001
                    try:
                        self._con.rollback()
                    except Exception:  # noqa: BLE001
                        pass
                    caida = pyodbc is not None and isinstance(
                        e, (pyodbc.OperationalError, pyodbc.InterfaceError))
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
                        except Exception:  # noqa: BLE001
                            pass
        return False, None

    # ================= Paso 1: la cédula pasó por la lectora =================
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
                request_json, _corta(url or config.API_URL, 300), http_status,
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
                    id_marca, _corta(url or config.API_URL, 300), request_json,
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

    # ================= Estado de los servicios externos =================
    def estado_servicio(self, servicio):
        """Última foto conocida de un servicio ('BAKELITE' / 'EXTERNA').
        Sirve para que la pantalla muestre la última conexión incluso recién
        arrancada la app."""
        def _fn(cur):
            cur.execute(
                "SELECT Servicio, EnLinea, UltimaConexionOk, UltimaFalla, UltimoError "
                "FROM dbo.EstadoServicios WHERE Servicio = ?", servicio)
            r = cur.fetchone()
            if not r:
                return None
            return {"servicio": r[0], "en_linea": bool(r[1]), "ultima_conexion": r[2],
                    "ultima_falla": r[3], "ultimo_error": r[4]}

        ok, res = self._ejecutar(_fn, "estado_servicio")
        return res if ok else None

    def marcar_servicio(self, servicio, en_linea, error=None):
        """Deja registrado si el servicio respondió o no."""
        def _fn(cur):
            cur.execute(
                "UPDATE dbo.EstadoServicios "
                "SET EnLinea = ?, FechaActualizacion = SYSDATETIME(), "
                "    UltimaConexionOk = CASE WHEN ? = 1 THEN SYSDATETIME() ELSE UltimaConexionOk END, "
                "    UltimaFalla = CASE WHEN ? = 0 THEN SYSDATETIME() ELSE UltimaFalla END, "
                "    UltimoError = CASE WHEN ? = 1 THEN NULL ELSE ? END "
                "WHERE Servicio = ?",
                1 if en_linea else 0, 1 if en_linea else 0, 1 if en_linea else 0,
                1 if en_linea else 0, _corta(error, 1000), servicio)
            return cur.rowcount > 0

        ok, res = self._ejecutar(_fn, "marcar_servicio")
        return bool(ok and res)

    # ================= Incidentes de conexión =================
    def abrir_incidente(self, servicio, error=None, fecha_deteccion=None):
        """Registra que un servicio se cayó. Si ya hay un incidente abierto para
        ese servicio, no crea otro: suma un intento fallido y actualiza el error.

        El UUID (`idIncidente` del contrato) se crea aquí, UNA sola vez, y se
        reutiliza en todos los reintentos del aviso: es la clave con la que la
        API deduplica.
        Devuelve el IdIncidente local."""
        fecha = fecha_deteccion or _ahora_iso()

        def _fn(cur):
            cur.execute(
                "SELECT IdIncidente FROM dbo.IncidentesConexion "
                "WHERE Servicio = ? AND FechaRecuperacion IS NULL", servicio)
            fila = cur.fetchone()
            if fila:
                id_incidente = int(fila[0])
                cur.execute(
                    "UPDATE dbo.IncidentesConexion "
                    "SET IntentosFallidos = IntentosFallidos + 1, UltimoError = ? "
                    "WHERE IdIncidente = ?", _corta(error, 1000), id_incidente)
                return id_incidente

            cur.execute(
                "INSERT INTO dbo.IncidentesConexion "
                "(IdIncidenteUuid, IdTerminal, Servicio, FechaDeteccion, PrimerError, UltimoError) "
                "OUTPUT INSERTED.IdIncidente VALUES (?,?,?,?,?,?)",
                uuid4().hex, config.ID_TERMINAL, servicio, fecha,
                _corta(error, 1000), _corta(error, 1000))
            return int(cur.fetchone()[0])

        ok, res = self._ejecutar(_fn, "abrir_incidente")
        return res if ok else None

    def cerrar_incidente(self, servicio, fecha_recuperacion=None):
        """El servicio volvió: cierra el incidente abierto y lo deja listo para
        avisarle a BakeliteApi. Devuelve el incidente cerrado, o None si no
        había ninguno abierto."""
        fecha = fecha_recuperacion or _ahora_iso()

        def _fn(cur):
            cur.execute(
                "SELECT IdIncidente FROM dbo.IncidentesConexion "
                "WHERE Servicio = ? AND FechaRecuperacion IS NULL", servicio)
            fila = cur.fetchone()
            if not fila:
                return None
            id_incidente = int(fila[0])
            cur.execute(
                "UPDATE dbo.IncidentesConexion SET FechaRecuperacion = ? WHERE IdIncidente = ?",
                fecha, id_incidente)
            cur.execute(
                "SELECT IdIncidente, Servicio, FechaDeteccion, FechaRecuperacion, "
                "       DuracionSegundos, IntentosFallidos, UltimoError, IdIncidenteUuid "
                "FROM dbo.IncidentesConexion WHERE IdIncidente = ?", id_incidente)
            r = cur.fetchone()
            return {"id": r[0], "servicio": r[1], "deteccion": r[2], "recuperacion": r[3],
                    "duracion_segundos": r[4], "intentos_fallidos": r[5],
                    "ultimo_error": r[6], "uuid": r[7]}

        ok, res = self._ejecutar(_fn, "cerrar_incidente")
        return res if ok else None

    def descartar_incidente(self, id_incidente, motivo):
        """Marca un incidente como no reportable a la API (NO_APLICA) sin
        borrarlo: sigue estando en la BD local para revisión."""
        def _fn(cur):
            cur.execute(
                "UPDATE dbo.IncidentesConexion "
                "SET EstadoEnvio = 'NO_APLICA', ErrorEnvio = ? WHERE IdIncidente = ?",
                _corta(motivo, 1000), id_incidente)
            return cur.rowcount > 0

        ok, res = self._ejecutar(_fn, "descartar_incidente")
        return bool(ok and res)

    def incidente_abierto(self, servicio):
        def _fn(cur):
            cur.execute(
                "SELECT IdIncidente, FechaDeteccion, IntentosFallidos, UltimoError "
                "FROM dbo.IncidentesConexion "
                "WHERE Servicio = ? AND FechaRecuperacion IS NULL", servicio)
            r = cur.fetchone()
            if not r:
                return None
            return {"id": r[0], "deteccion": r[1], "intentos_fallidos": r[2],
                    "ultimo_error": r[3]}

        ok, res = self._ejecutar(_fn, "incidente_abierto")
        return res if ok else None

    def incidentes_por_avisar(self, tope=20):
        """Cortes ya recuperados que todavía no se le informaron a BakeliteApi."""
        def _fn(cur):
            cur.execute(
                "SELECT TOP (?) IdIncidente, Servicio, FechaDeteccion, FechaRecuperacion, "
                "       DuracionSegundos, IntentosFallidos, UltimoError, IntentosEnvio, "
                "       IdIncidenteUuid "
                "FROM dbo.IncidentesConexion "
                "WHERE EstadoEnvio = 'PENDIENTE' AND FechaRecuperacion IS NOT NULL "
                "ORDER BY IdIncidente", tope)
            return [{"id": r[0], "servicio": r[1], "deteccion": r[2], "recuperacion": r[3],
                     "duracion_segundos": r[4], "intentos_fallidos": r[5],
                     "ultimo_error": r[6], "intentos_envio": r[7], "uuid": r[8]}
                    for r in cur.fetchall()]

        ok, res = self._ejecutar(_fn, "incidentes_por_avisar")
        return res if ok else []

    def registrar_aviso_incidente(self, id_incidente, clasificacion, http_status=None,
                                  respuesta=None, error=None, id_registro_api=None,
                                  estado_api=None):
        """Resultado de avisarle el corte a BakeliteApi. Guarda también el
        `idRegistro` y el `estado` (REGISTRADO / DUPLICADO) que devuelve la API.
        clasificacion: 'OK' | 'PERMANENTE' | 'REINTENTAR'."""
        estado = {"OK": "ENVIADO", "PERMANENTE": "FALLIDO"}.get(clasificacion, "PENDIENTE")

        def _fn(cur):
            cur.execute(
                "UPDATE dbo.IncidentesConexion "
                "SET EstadoEnvio = ?, IntentosEnvio = IntentosEnvio + 1, "
                "    UltimoIntentoEnvio = SYSDATETIME(), "
                "    FechaEnvio = CASE WHEN ? = 'ENVIADO' THEN SYSDATETIME() ELSE FechaEnvio END, "
                "    HttpStatusEnvio = ?, RespuestaEnvio = ?, "
                "    IdRegistroApi = COALESCE(?, IdRegistroApi), "
                "    EstadoApi = COALESCE(?, EstadoApi), "
                "    ErrorEnvio = CASE WHEN ? = 'ENVIADO' THEN NULL ELSE ? END "
                "WHERE IdIncidente = ?",
                estado, estado, http_status, respuesta, id_registro_api, estado_api,
                estado, _corta(error, 1000), id_incidente)
            return cur.rowcount > 0

        ok, res = self._ejecutar(_fn, "registrar_aviso_incidente")
        return bool(ok and res)

    def recuperar_marca(self, ev):
        """Rehace en la BD una marca que quedó solo en la cola JSON porque la BD
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

    # ================= Terminal =================
    # El nombre del terminal se sincroniza con Bakelite en ambos sentidos y gana
    # el cambio más reciente. Por eso NombreFecha se trata aparte de
    # FechaModificacion: cambiar la ubicación no debe hacer que un nombre viejo
    # gane la comparación. Ver CONTRATO_SINCRONIZACION_NOMBRE_TERMINAL.md.
    def terminal(self, id_terminal=None):
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal

        def _fn(cur):
            cur.execute("SELECT IdTerminal, Nombre, Ubicacion, Activo, "
                        "       NombreFecha, NombreOrigen, NombrePor, NombreSincronizado, "
                        "       ArduinoConectado, ArduinoPuerto "
                        "FROM dbo.Terminales WHERE IdTerminal = ?", idt)
            r = cur.fetchone()
            if not r:
                return None
            return {"id": r[0], "nombre": r[1], "ubicacion": r[2], "activo": bool(r[3]),
                    "nombre_fecha": r[4], "nombre_origen": r[5], "nombre_por": r[6],
                    "nombre_sincronizado": bool(r[7]),
                    "arduino_conectado": None if r[8] is None else bool(r[8]),
                    "arduino_puerto": r[9]}

        ok, res = self._ejecutar(_fn, "terminal")
        return res if ok else None

    def renombrar_terminal(self, nombre, ubicacion=None, usuario=None, id_terminal=None):
        """Cambia el nombre del terminal desde esta app (origen LOCAL).

        Sella la hora del cambio en NombreFecha y deja NombreSincronizado = 0:
        el sincronizador lo subirá a Bakelite. Si no hay red, el nombre igual
        queda cambiado en pantalla y el envío espera, siempre con esta fecha.
        Devuelve el dict del terminal ya actualizado, o None si falló.
        """
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal
        nombre = (nombre or "").strip()
        if not nombre:
            log.error("El nombre del terminal no puede quedar vacío.")
            return None
        nombre = _corta(nombre, 150)

        def _fn(cur):
            cur.execute(
                "UPDATE dbo.Terminales "
                "SET Nombre = ?, Ubicacion = COALESCE(?, Ubicacion), "
                "    NombreFecha = SYSDATETIMEOFFSET(), NombreOrigen = 'LOCAL', "
                "    NombrePor = ?, NombreSincronizado = 0, "
                "    ModificadoPor = ?, FechaModificacion = SYSDATETIME() "
                "WHERE IdTerminal = ?",
                nombre, _corta(ubicacion, 200), _corta(usuario, 100),
                _corta(usuario, 100), idt)
            if cur.rowcount <= 0:
                return None
            cur.execute("SELECT Nombre, NombreFecha, NombrePor "
                        "FROM dbo.Terminales WHERE IdTerminal = ?", idt)
            r = cur.fetchone()
            return {"id": idt, "nombre": r[0], "nombre_fecha": r[1],
                    "nombre_origen": "LOCAL", "nombre_por": r[2],
                    "nombre_sincronizado": False}

        ok, res = self._ejecutar(_fn, "renombrar_terminal")
        if ok and res:
            log.info("Terminal renombrado a %r (pendiente de subir a Bakelite).",
                     res["nombre"])
            return res
        return None

    def aplicar_nombre_remoto(self, nombre, nombre_fecha, nombre_por=None,
                              id_terminal=None):
        """Adopta el nombre que mandó Bakelite, conservando SU fecha.

        Guardar la hora de aplicación en vez de la recibida haría que la copia
        local pareciera siempre más nueva y el cambio rebotaría de vuelta en la
        comparación siguiente. El WHERE es la guarda del contrato: nunca pisar
        un nombre local más nuevo, aunque el ciclo llegue tarde o desordenado.

        Devuelve True si se aplicó, False si el local ya era más nuevo o igual.
        """
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal
        nombre = (nombre or "").strip()
        if not nombre or not nombre_fecha:
            log.error("Nombre remoto inválido: %r / %r", nombre, nombre_fecha)
            return False
        fecha = _texto_fecha(nombre_fecha)

        def _fn(cur):
            cur.execute(
                "UPDATE dbo.Terminales "
                "SET Nombre = ?, NombreFecha = ?, NombreOrigen = 'API', "
                "    NombrePor = ?, NombreSincronizado = 1 "
                "WHERE IdTerminal = ? AND NombreFecha < ?",
                _corta(nombre, 150), fecha, _corta(nombre_por, 100), idt, fecha)
            return cur.rowcount > 0

        ok, res = self._ejecutar(_fn, "aplicar_nombre_remoto")
        if ok and res:
            log.info("Nombre del terminal adoptado desde Bakelite: %r (%s).",
                     nombre, fecha)
        return bool(ok and res)

    def marcar_nombre_sincronizado(self, id_terminal=None):
        """El nombre local ya está en Bakelite: deja de estar pendiente.

        Solo limpia la marca si NombreFecha no cambió mientras se subía; si el
        operador renombró de nuevo en ese intervalo, el pendiente sigue vivo.
        """
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal

        def _fn(cur):
            cur.execute("UPDATE dbo.Terminales SET NombreSincronizado = 1 "
                        "WHERE IdTerminal = ?", idt)
            return cur.rowcount > 0

        ok, res = self._ejecutar(_fn, "marcar_nombre_sincronizado")
        return bool(ok and res)

    # ================= Lectoras y relés =================
    # Dos cosas distintas conviven en estas tablas:
    #   - CONFIGURACIÓN: qué dispositivo es ENTRADA o SALIDA. La decide una
    #     persona, aquí o en la web, y se sincroniza en ambos sentidos.
    #   - ESTADO: si está conectado, en qué puerto, cuándo leyó. Es un hecho
    #     físico que solo observa este equipo.
    # Ver CONTRATO_DISPOSITIVOS_TERMINAL.md.
    def lectoras(self, id_terminal=None):
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal

        def _fn(cur):
            cur.execute(
                "SELECT Numero, Sentido, Descripcion, UltimoPuerto, Activa, "
                "       ConfigFecha, ConfigOrigen, ConfigPor, Sincronizado, "
                "       Conectada, UltimaLectura, UltimoError, Ancla "
                "FROM dbo.Lectoras WHERE IdTerminal = ? ORDER BY Numero", idt)
            return [{"numero": r[0], "sentido": r[1], "descripcion": r[2],
                     "puerto": r[3], "activa": bool(r[4]),
                     "config_fecha": r[5], "config_origen": r[6],
                     "config_por": r[7], "sincronizado": bool(r[8]),
                     "conectada": None if r[9] is None else bool(r[9]),
                     "ultima_lectura": r[10], "ultimo_error": r[11],
                     "ancla": r[12]}
                    for r in cur.fetchall()]

        ok, res = self._ejecutar(_fn, "lectoras")
        return res if ok else []

    def reles(self, id_terminal=None):
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal

        def _fn(cur):
            cur.execute(
                "SELECT Numero, Sentido, Comando, Descripcion, Activo, "
                "       ConfigFecha, ConfigOrigen, ConfigPor, Sincronizado, "
                "       UltimoDisparo, UltimoError "
                "FROM dbo.Reles WHERE IdTerminal = ? ORDER BY Numero", idt)
            return [{"numero": r[0], "sentido": r[1], "comando": r[2],
                     "descripcion": r[3], "activo": bool(r[4]),
                     "config_fecha": r[5], "config_origen": r[6],
                     "config_por": r[7], "sincronizado": bool(r[8]),
                     "ultimo_disparo": r[9], "ultimo_error": r[10]}
                    for r in cur.fetchall()]

        ok, res = self._ejecutar(_fn, "reles")
        return res if ok else []

    # ---- Configuración (se sincroniza) ----
    def set_sentido_lectora(self, numero, sentido, usuario=None):
        """Marca una lectora como ENTRADA o SALIDA; la otra recibe el contrario."""
        return self._set_sentido("dbo.Lectoras", numero, sentido, usuario)

    def set_sentido_rele(self, numero, sentido, usuario=None):
        """Marca un relé como ENTRADA o SALIDA; el otro recibe el contrario."""
        return self._set_sentido("dbo.Reles", numero, sentido, usuario)

    def _set_sentido(self, tabla, numero, sentido, usuario=None, id_terminal=None):
        """El índice único garantiza una ENTRADA y una SALIDA por terminal, así
        que el cambio va en UNA sentencia con CASE: intercambia los dos a la vez
        y el motor valida el unique recién al terminarla. En dos UPDATE
        reventaría en el medio.

        Sella ConfigFecha y deja Sincronizado = 0: el cambio queda pendiente de
        subir a Bakelite, y esa fecha es la que decide quién gana si allá también
        lo cambiaron.
        """
        if sentido not in ("E", "S"):
            log.error("Sentido inválido: %r (debe ser E o S).", sentido)
            return False
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal
        contrario = "S" if sentido == "E" else "E"

        def _fn(cur):
            cur.execute(
                f"UPDATE {tabla} "
                "SET Sentido = CASE WHEN Numero = ? THEN ? ELSE ? END, "
                "    ConfigFecha = SYSDATETIMEOFFSET(), ConfigOrigen = 'LOCAL', "
                "    ConfigPor = ?, Sincronizado = 0, "
                "    FechaModificacion = SYSDATETIME(), ModificadoPor = ? "
                "WHERE IdTerminal = ?",
                numero, sentido, contrario, _corta(usuario, 100),
                _corta(usuario, 100), idt)
            return cur.rowcount > 0

        ok, res = self._ejecutar(_fn, f"set_sentido {tabla}")
        if ok and res:
            log.info("%s: el número %s queda como %s (pendiente de subir).", tabla,
                     numero, "ENTRADA" if sentido == "E" else "SALIDA")
        return bool(ok and res)

    def aplicar_config_remota(self, tipo, numero, sentido, config_fecha,
                              descripcion=None, activo=None, config_por=None,
                              id_terminal=None):
        """Adopta la configuración que mandó Bakelite, conservando SU fecha.

        Cambiar un sentido siempre mueve a los DOS dispositivos: si la API dice
        que la lectora 1 es ENTRADA, la 2 pasa a SALIDA por definición. Hacerlo
        de a uno viola el índice único a mitad de camino, así que va en una sola
        sentencia con CASE y el motor valida al terminarla.

        Al otro dispositivo se le pone la misma fecha y origen: su sentido
        también cambió, y dejarlo con la fecha vieja haría que la comparación
        siguiente lo viera como una diferencia y lo mandara de vuelta.

        La guarda `ConfigFecha < @fecha` sobre el dispositivo nombrado es la que
        impide pisar un cambio local más nuevo si los mensajes llegan
        desordenados. Guardar la hora de aplicación en vez de la recibida haría
        que la copia local pareciera siempre más nueva y el cambio rebotaría.
        """
        if sentido not in ("E", "S"):
            log.error("Sentido inválido desde la API: %r", sentido)
            return False
        tabla = "dbo.Lectoras" if tipo == "lectora" else "dbo.Reles"
        col_activo = "Activa" if tipo == "lectora" else "Activo"
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal
        fecha = _texto_fecha(config_fecha)
        contrario = "S" if sentido == "E" else "E"

        def _fn(cur):
            cur.execute(
                f"UPDATE {tabla} "
                "SET Sentido = CASE WHEN Numero = ? THEN ? ELSE ? END, "
                "    Descripcion = CASE WHEN Numero = ? THEN COALESCE(?, Descripcion) "
                "                       ELSE Descripcion END, "
                f"    {col_activo} = CASE WHEN Numero = ? THEN COALESCE(?, {col_activo}) "
                f"                        ELSE {col_activo} END, "
                "    ConfigFecha = ?, ConfigOrigen = 'API', ConfigPor = ?, "
                "    Sincronizado = 1 "
                "WHERE IdTerminal = ? "
                f"  AND EXISTS (SELECT 1 FROM {tabla} d "
                "               WHERE d.IdTerminal = ? AND d.Numero = ? "
                "                 AND d.ConfigFecha < ?)",
                numero, sentido, contrario,
                numero, _corta(descripcion, 150),
                numero, activo,
                fecha, _corta(config_por, 100),
                idt, idt, numero, fecha)
            return cur.rowcount > 0

        ok, res = self._ejecutar(_fn, "aplicar_config_remota")
        if ok and res:
            log.info("%s %s adoptado desde Bakelite: %s (%s). El otro pasa a %s.",
                     tipo, numero, sentido, fecha, contrario)
        return bool(ok and res)

    def dispositivos_pendientes(self, id_terminal=None):
        """Configuración cambiada aquí que todavía no llegó a Bakelite."""
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal
        return {"lectoras": [d for d in self.lectoras(idt) if not d["sincronizado"]],
                "reles": [d for d in self.reles(idt) if not d["sincronizado"]]}

    def marcar_dispositivos_sincronizados(self, tipo, numeros, id_terminal=None):
        """Lo subido deja de estar pendiente. Solo se limpia si la fecha no
        cambió mientras se subía: si el operador tocó el sentido en ese
        intervalo, el pendiente sigue vivo."""
        if not numeros:
            return True
        tabla = "dbo.Lectoras" if tipo == "lectora" else "dbo.Reles"
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal
        marcas = ",".join("?" for _ in numeros)

        def _fn(cur):
            cur.execute(
                f"UPDATE {tabla} SET Sincronizado = 1 "
                f"WHERE IdTerminal = ? AND Numero IN ({marcas})",
                idt, *numeros)
            return cur.rowcount

        ok, res = self._ejecutar(_fn, "marcar_dispositivos_sincronizados")
        return bool(ok and res)

    # ---- Estado observado (solo lo escribe este equipo) ----
    def estado_lectora(self, numero, conectada=None, puerto=None, error=None,
                       ancla=None, id_terminal=None):
        """Guarda lo que se ve del hardware. Lo escribe la detección de puertos
        cada vez que algo cambia, para que la pantalla y Bakelite muestren el
        estado real y no el último que alguien recuerde.

        Al reconectar se BORRA el último error. Antes solo se sobrescribía si
        llegaba uno nuevo, así que una lectora que se desenchufaba y se volvía a
        enchufar quedaba conectada pero arrastrando el "Se desconectó del
        puerto": en la web seguía viéndose como si algo anduviera mal.
        """
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal
        limpiar_error = bool(conectada) and error is None

        def _fn(cur):
            cur.execute(
                "UPDATE dbo.Lectoras "
                "SET Conectada = COALESCE(?, Conectada), "
                "    UltimoPuerto = CASE WHEN ? IS NULL THEN UltimoPuerto ELSE ? END, "
                "    UltimoError = CASE WHEN ? = 1 THEN NULL "
                "                       WHEN ? IS NULL THEN UltimoError ELSE ? END, "
                "    Ancla = CASE WHEN ? IS NULL THEN Ancla ELSE ? END, "
                "    EstadoFecha = SYSDATETIMEOFFSET() "
                "WHERE IdTerminal = ? AND Numero = ?",
                conectada, puerto, _corta(puerto, 100),
                limpiar_error, error, _corta(error, 500),
                ancla, _corta(ancla, 200), idt, numero)
            return cur.rowcount > 0

        ok, res = self._ejecutar(_fn, "estado_lectora")
        return bool(ok and res)

    def registrar_lectura(self, numero, id_terminal=None):
        """Momento de la última lectura de esa lectora."""
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal

        def _fn(cur):
            cur.execute(
                "UPDATE dbo.Lectoras "
                "SET UltimaLectura = SYSDATETIMEOFFSET(), Conectada = 1, "
                "    EstadoFecha = SYSDATETIMEOFFSET() "
                "WHERE IdTerminal = ? AND Numero = ?", idt, numero)
            return cur.rowcount > 0

        ok, res = self._ejecutar(_fn, "registrar_lectura")
        return bool(ok and res)

    def registrar_disparo_rele(self, numero, error=None, id_terminal=None):
        """Momento del último accionamiento de ese relé."""
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal

        def _fn(cur):
            cur.execute(
                "UPDATE dbo.Reles "
                "SET UltimoDisparo = SYSDATETIMEOFFSET(), "
                "    UltimoError = CASE WHEN ? IS NULL THEN NULL ELSE ? END, "
                "    EstadoFecha = SYSDATETIMEOFFSET() "
                "WHERE IdTerminal = ? AND Numero = ?",
                error, _corta(error, 500), idt, numero)
            return cur.rowcount > 0

        ok, res = self._ejecutar(_fn, "registrar_disparo_rele")
        return bool(ok and res)

    def estado_arduino(self, conectado, puerto=None, id_terminal=None):
        """Hay un Arduino por terminal, así que su estado vive en Terminales."""
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal

        def _fn(cur):
            cur.execute(
                "UPDATE dbo.Terminales "
                "SET ArduinoConectado = ?, "
                "    ArduinoPuerto = CASE WHEN ? IS NULL THEN ArduinoPuerto ELSE ? END, "
                "    ArduinoEstadoFecha = SYSDATETIMEOFFSET() "
                "WHERE IdTerminal = ?",
                bool(conectado), puerto, _corta(puerto, 100), idt)
            return cur.rowcount > 0

        ok, res = self._ejecutar(_fn, "estado_arduino")
        return bool(ok and res)

    def actualizar_puerto_lectora(self, numero, puerto):
        """Compatibilidad: la detección de puertos ya llamaba a esto."""
        return self.estado_lectora(numero, conectada=puerto is not None,
                                   puerto=puerto)

    # ================= Versiones =================    # ================= Versiones =================
    def version_activa(self):
        def _fn(cur):
            cur.execute("SELECT TOP (1) IdVersion, Numero, SubidoPor, FechaSubida, Notas "
                        "FROM dbo.Versiones WHERE Activo = 1")
            r = cur.fetchone()
            if not r:
                return None
            return {"id": r[0], "numero": r[1], "subido_por": r[2],
                    "fecha": r[3], "notas": r[4]}

        ok, res = self._ejecutar(_fn, "version_activa")
        return res if ok else None

    def versiones(self):
        def _fn(cur):
            cur.execute("SELECT IdVersion, Numero, SubidoPor, FechaSubida, Notas, Activo "
                        "FROM dbo.Versiones ORDER BY FechaSubida DESC")
            return [{"id": r[0], "numero": r[1], "subido_por": r[2], "fecha": r[3],
                     "notas": r[4], "activo": bool(r[5])} for r in cur.fetchall()]

        ok, res = self._ejecutar(_fn, "versiones")
        return res if ok else []

    def registrar_version(self, numero, subido_por, notas=None, activar=True):
        """Da de alta una versión y, si `activar`, la deja como la única activa."""
        def _fn(cur):
            cur.execute("SELECT IdVersion FROM dbo.Versiones WHERE Numero = ?", numero)
            fila = cur.fetchone()
            if fila:
                id_version = int(fila[0])
                cur.execute("UPDATE dbo.Versiones SET SubidoPor = ?, "
                            "Notas = COALESCE(?, Notas), FechaSubida = SYSDATETIME() "
                            "WHERE IdVersion = ?", subido_por, notas, id_version)
            else:
                cur.execute("INSERT INTO dbo.Versiones (Numero, SubidoPor, Notas, Activo) "
                            "OUTPUT INSERTED.IdVersion VALUES (?,?,?,0)",
                            _corta(numero, 30), _corta(subido_por, 100), _corta(notas, 500))
                id_version = int(cur.fetchone()[0])

            if activar:
                # El orden importa: primero se apaga la activa, si no el índice
                # único filtrado (UX_Versiones_UnaActiva) rechaza el UPDATE.
                cur.execute("UPDATE dbo.Versiones SET Activo = 0 WHERE Activo = 1")
                cur.execute("UPDATE dbo.Versiones SET Activo = 1 WHERE IdVersion = ?", id_version)
            return id_version

        ok, res = self._ejecutar(_fn, "registrar_version")
        return res if ok else None

    def activar_version(self, numero):
        def _fn(cur):
            cur.execute("SELECT IdVersion FROM dbo.Versiones WHERE Numero = ?", numero)
            fila = cur.fetchone()
            if not fila:
                raise ValueError(f"La versión {numero} no está registrada")
            cur.execute("UPDATE dbo.Versiones SET Activo = 0 WHERE Activo = 1")
            cur.execute("UPDATE dbo.Versiones SET Activo = 1 WHERE IdVersion = ?", fila[0])
            return True

        ok, _ = self._ejecutar(_fn, "activar_version")
        return ok


# ================= Utilidades =================
def _ahora_iso():
    """Fecha/hora local con offset (Chile), como la usa el contrato."""
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _texto_fecha(valor):
    """Fecha para un parámetro DATETIMEOFFSET. pyodbc no enlaza ese tipo, así
    que va como texto ISO y SQL Server lo convierte (igual que los incidentes)."""
    if isinstance(valor, datetime.datetime):
        if valor.tzinfo is None:
            valor = valor.astimezone()
        return valor.isoformat(timespec="seconds")
    return str(valor).strip()


def _corta(texto, largo):
    if texto is None:
        return None
    texto = str(texto)
    return texto[:largo] if len(texto) > largo else texto


def _evento_largo(evento):
    """'E'/'S' de la app -> 'ENTRADA'/'SALIDA' del contrato."""
    if evento in ("ENTRADA", "SALIDA"):
        return evento
    return "SALIDA" if evento == "S" else "ENTRADA"


def probar_conexion():
    """Diagnóstico rápido: python3 basedatos.py"""
    bd = BDLocal()
    print("Drivers ODBC:", drivers_disponibles() or "ninguno instalado")
    print("Driver usado:", elegir_driver())
    if not bd.disponible:
        print("SIN CONEXIÓN:", bd.ultimo_error)
        return False
    print("Conectado a", config.SQL_BASE, "en", config.SQL_SERVIDOR)
    print("Terminal :", bd.terminal())
    print("Versión  :", bd.version_activa())
    print("Resumen  :", bd.resumen())
    bd.cerrar()
    return True


if __name__ == "__main__":
    probar_conexion()
