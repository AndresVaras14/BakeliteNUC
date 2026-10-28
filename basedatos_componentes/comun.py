# -*- coding: utf-8 -*-
"""Utilidades compartidas por los repositorios de la base local (SQLite)."""

import datetime


def _ahora_iso():
    """Fecha/hora local con offset (Chile), como la usa el contrato."""
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _texto_fecha(valor):
    """Fecha como texto ISO. SQLite no tiene tipo fecha propio: todo se
    compara como texto, así que el formato tiene que ser SIEMPRE el mismo
    (mismo padding, mismo offset) o un ORDER BY/rango sale mal sin avisar."""
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


def _bin(valor):
    """bool/None -> 0/1/None, para escribir en una columna INTEGER."""
    return None if valor is None else (1 if valor else 0)


def _leer_bool(valor):
    """0/1/None de SQLite -> bool/None."""
    return None if valor is None else bool(valor)
