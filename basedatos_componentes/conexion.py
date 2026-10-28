# -*- coding: utf-8 -*-
"""Conexión y ejecución segura contra la BD local en SQLite.

Reemplaza la conexión a SQL Server (BakeliteTorniquete). No hay reconexión ni
reintento por caída de red -- SQLite es un archivo local, no un servicio que
se pueda caer de forma independiente del propio proceso. Lo único que puede
fallar de verdad es el archivo (disco lleno, permisos, corrupción), y de eso
ya se encarga ``almacen_sqlite.conectar`` con su cuarentena automática.
"""

import logging
import threading

from almacen_sqlite import conectar

log = logging.getLogger("basedatos")


class ConexionMixin:
    """Ciclo de vida de BDLocal sobre SQLite."""

    def __init__(self, ruta=None):
        self._lock = threading.RLock()
        self.disponible = False
        self.ultimo_error = None
        self._con = None
        self.conectar(ruta)

    def conectar(self, ruta=None):
        """Abre (o reabre) la base. Devuelve True si quedó utilizable."""
        with self._lock:
            self._cerrar_silencioso()
            try:
                self._con = conectar(ruta) if ruta else conectar()
                self.disponible = True
                self.ultimo_error = None
                return True
            except Exception as e:  # noqa: BLE001
                self._con = None
                self.disponible = False
                self.ultimo_error = str(e)
                log.critical("No se pudo abrir la BD local (SQLite): %s", e)
                return False

    def cerrar(self):
        with self._lock:
            self._cerrar_silencioso()
            self.disponible = False

    def _cerrar_silencioso(self):
        if self._con is not None:
            try:
                self._con.close()
            except Exception as e:  # noqa: BLE001
                log.debug("Error cerrando la BD local: %s", e)
            self._con = None

    def _ejecutar(self, fn, descripcion):
        """Corre `fn(cursor)` dentro de una transacción. Devuelve (ok, resultado).

        Mismo nombre y firma que la versión SQL Server, para que los mixins
        que la usan (marcas, incidentes, terminal, dispositivos) no cambien
        ni una línea de cómo la llaman.
        """
        with self._lock:
            if self._con is None and not self.conectar():
                return False, None
            try:
                cur = self._con.cursor()
                res = fn(cur)
                self._con.commit()
                return True, res
            except Exception as e:  # noqa: BLE001
                try:
                    self._con.rollback()
                except Exception as rollback_error:  # noqa: BLE001
                    log.debug("Rollback falló durante %s: %s", descripcion, rollback_error)
                self.ultimo_error = str(e)
                log.error("Error de BD local en %s: %s", descripcion, e)
                return False, None
