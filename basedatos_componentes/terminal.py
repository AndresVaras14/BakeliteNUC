# -*- coding: utf-8 -*-
import json
import logging
from uuid import uuid4

import config
from .comun import _ahora_iso, _corta, _evento_largo, _texto_fecha

log = logging.getLogger("basedatos")

class TerminalMixin:
    """Identidad del terminal, nombre sincronizado y versiones."""

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

