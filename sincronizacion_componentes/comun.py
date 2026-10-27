# -*- coding: utf-8 -*-
"""Utilidades compartidas por los componentes del sincronizador."""

import datetime


def _iso(valor):
    """Fecha de la BD -> texto ISO 8601 para el payload."""
    if valor is None:
        return None
    if isinstance(valor, datetime.datetime):
        if valor.tzinfo is None:
            valor = valor.astimezone()
        return valor.isoformat(timespec="seconds")
    return str(valor)
