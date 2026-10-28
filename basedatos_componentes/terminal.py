# -*- coding: utf-8 -*-
"""Identidad del terminal y sincronización del nombre (SQLite)."""

import logging

import config
from .comun import _ahora_iso, _corta, _leer_bool, _texto_fecha

log = logging.getLogger("basedatos")


class TerminalMixin:
    """Identidad del terminal y nombre sincronizado con Bakelite."""

    def _asegurar_terminal(self, cur, idt):
        """La fila del terminal se crea sola la primera vez que se la pide,
        con el nombre y número que ya definió identidad_terminal.json -- no
        hace falta un script de instalación aparte como con SQL Server."""
        cur.execute("SELECT 1 FROM Terminal WHERE IdTerminal = ?", (idt,))
        if cur.fetchone():
            return
        nombre = _corta(config.NOMBRE_TERMINAL_DEFECTO, 150) or f"Torniquete {idt}"
        cur.execute(
            "INSERT INTO Terminal (IdTerminal, Nombre, NombreFecha, NombreOrigen) "
            "VALUES (?, ?, ?, 'LOCAL')",
            (idt, nombre, _ahora_iso()))

    def terminal(self, id_terminal=None):
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal

        def _fn(cur):
            self._asegurar_terminal(cur, idt)
            cur.execute(
                "SELECT IdTerminal, Nombre, Ubicacion, Activo, "
                "       NombreFecha, NombreOrigen, NombrePor, NombreSincronizado, "
                "       ArduinoConectado, ArduinoPuerto "
                "FROM Terminal WHERE IdTerminal = ?", (idt,))
            r = cur.fetchone()
            if not r:
                return None
            return {"id": r[0], "nombre": r[1], "ubicacion": r[2], "activo": bool(r[3]),
                    "nombre_fecha": r[4], "nombre_origen": r[5], "nombre_por": r[6],
                    "nombre_sincronizado": bool(r[7]),
                    "arduino_conectado": _leer_bool(r[8]), "arduino_puerto": r[9]}

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
            self._asegurar_terminal(cur, idt)
            fecha = _ahora_iso()
            cur.execute(
                "UPDATE Terminal "
                "SET Nombre = ?, Ubicacion = COALESCE(?, Ubicacion), "
                "    NombreFecha = ?, NombreOrigen = 'LOCAL', NombrePor = ?, "
                "    NombreSincronizado = 0 "
                "WHERE IdTerminal = ?",
                (nombre, _corta(ubicacion, 200), fecha, _corta(usuario, 100), idt))
            if cur.rowcount <= 0:
                return None
            return {"id": idt, "nombre": nombre, "nombre_fecha": fecha,
                    "nombre_origen": "LOCAL", "nombre_por": _corta(usuario, 100),
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
            self._asegurar_terminal(cur, idt)
            cur.execute(
                "UPDATE Terminal "
                "SET Nombre = ?, NombreFecha = ?, NombreOrigen = 'API', "
                "    NombrePor = ?, NombreSincronizado = 1 "
                "WHERE IdTerminal = ? AND NombreFecha < ?",
                (_corta(nombre, 150), fecha, _corta(nombre_por, 100), idt, fecha))
            return cur.rowcount > 0

        ok, res = self._ejecutar(_fn, "aplicar_nombre_remoto")
        if ok and res:
            log.info("Nombre del terminal adoptado desde Bakelite: %r (%s).", nombre, fecha)
        return bool(ok and res)

    def marcar_nombre_sincronizado(self, id_terminal=None):
        """El nombre local ya está en Bakelite: deja de estar pendiente."""
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal

        def _fn(cur):
            cur.execute("UPDATE Terminal SET NombreSincronizado = 1 WHERE IdTerminal = ?",
                        (idt,))
            return cur.rowcount > 0

        ok, res = self._ejecutar(_fn, "marcar_nombre_sincronizado")
        return bool(ok and res)

    def version_activa(self):
        """Bakelite lleva el historial completo de versiones
        (dbo.SistemaVersiones); acá no hace falta duplicarlo -- devuelve
        siempre None, el llamador (el log de arranque) ya lo contempla."""
        return None
