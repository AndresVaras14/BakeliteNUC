# -*- coding: utf-8 -*-
"""Fachada compatible de la base SQL Server local del torniquete.

Las responsabilidades viven en ``basedatos_componentes``. El resto de la
aplicación continúa importando ``BDLocal`` desde aquí, por lo que la división
no altera ningún llamador ni el comportamiento offline.
"""

import config
from basedatos_componentes.conexion import (
    ConexionMixin,
    cadena_conexion,
    drivers_disponibles,
    elegir_driver,
)
from basedatos_componentes.dispositivos import DispositivosMixin
from basedatos_componentes.incidentes import IncidentesMixin
from basedatos_componentes.marcas import MarcasMixin
from basedatos_componentes.terminal import TerminalMixin


class BDLocal(
    ConexionMixin,
    MarcasMixin,
    IncidentesMixin,
    TerminalMixin,
    DispositivosMixin,
):
    """Acceso unificado a SQL Server; reconecta y no propaga sus caídas."""


def probar_conexion():
    """Diagnóstico rápido: ``python basedatos.py``."""
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
