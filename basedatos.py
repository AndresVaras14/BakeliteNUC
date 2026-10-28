# -*- coding: utf-8 -*-
"""Fachada compatible de la BD local del torniquete (SQLite).

Las responsabilidades viven en ``basedatos_componentes``. El resto de la
aplicación continúa importando ``BDLocal`` desde aquí, por lo que la división
no altera ningún llamador ni el comportamiento offline. Reemplaza lo que antes
era SQL Server (BakeliteTorniquete): ya no hay servidor que instalar, driver
ODBC que configurar ni contraseña de BD local que guardar -- todo vive en el
mismo archivo que ya usan la cola de marcas y la bitácora.
"""

import config
from basedatos_componentes.conexion import ConexionMixin
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
    """Acceso unificado a la BD local en SQLite."""


def probar_conexion():
    """Diagnóstico rápido: ``python basedatos.py``."""
    bd = BDLocal()
    if not bd.disponible:
        print("SIN CONEXIÓN:", bd.ultimo_error)
        return False
    print("Archivo  :", config.ARCHIVO_SQLITE_LOCAL)
    print("Terminal :", bd.terminal())
    print("Lectoras :", bd.lectoras())
    print("Relés    :", bd.reles())
    bd.cerrar()
    return True


if __name__ == "__main__":
    probar_conexion()
