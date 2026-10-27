# -*- coding: utf-8 -*-
import json
import logging
from uuid import uuid4

import config
from .comun import _ahora_iso, _corta, _evento_largo, _texto_fecha

log = logging.getLogger("basedatos")

class IncidentesMixin:
    """Estado de servicios e incidentes de conectividad."""

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

