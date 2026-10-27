# -*- coding: utf-8 -*-
"""Utilidades compartidas por los repositorios de la base local."""

import datetime

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

