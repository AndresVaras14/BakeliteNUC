# -*- coding: utf-8 -*-
"""
Registro persistente de todo lo que hace el sistema.

RegistroStore escribe CADA evento en `registros.json` con dos banderas:
    subido_local (0/1)  -> si ya se guardó en la BD local (SQL Server)
    subido_api   (0/1)  -> si ya se subió a la API

El JSON es la bandeja de salida: se escribe ANTES de intentar cualquier envío,
así que si el equipo se queda sin BD o sin red no se pierde ninguna marca; lo
que quede en 0 lo reintenta el Sincronizador.

La BD local vive en basedatos.BDLocal (SQL Server, base BakeliteTorniquete).
"""

import os
import json
import logging
import threading
import datetime
from uuid import uuid4

import config
from rut import formatea_rut

log = logging.getLogger("registros")


def _ahora():
    # Fecha/hora local CON offset (Chile), como exige el contrato (ISO 8601).
    return datetime.datetime.now().astimezone()


class RegistroStore:
    """Bandeja de salida en JSON, con banderas de subida."""

    def __init__(self, ruta=None):
        self.ruta = ruta or config.ARCHIVO_REGISTROS
        self._lock = threading.RLock()
        self.registros = []
        self._next_id = 1
        self._cargar()

    def _cargar(self):
        try:
            with open(self.ruta, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.registros = data.get("registros", [])
        except FileNotFoundError:
            self.registros = []
        except Exception as e:  # noqa: BLE001
            log.error("No se pudo leer %s: %s (se parte vacío)", self.ruta, e)
            self.registros = []
        self._next_id = 1 + max((r.get("id", 0) for r in self.registros), default=0)
        pend = sum(1 for r in self.registros
                   if not r.get("subido_local") or not r.get("subido_api"))
        log.info("Registros: %d en total, %d pendientes de subir.",
                 len(self.registros), pend)

    def _guardar(self):
        tmp = self.ruta + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"registros": self.registros}, f,
                          indent=2, ensure_ascii=False)
            os.replace(tmp, self.ruta)   # escritura atómica
        except Exception as e:  # noqa: BLE001
            log.error("No se pudo guardar %s: %s", self.ruta, e)

    def registrar(self, rut, nombre, sentido, codigo, autorizado, ubicacion="", motivo="",
                  id_evento=None, id_marca_local=None):
        """Agrega una marca nueva (con banderas en 0), construye el payload del
        contrato con un idEvento UUID único, y persiste TODO antes de enviar.
        Devuelve el evento. `rut` llega normalizado (rut_norm).

        `id_evento` viene de la BD local, que ya creó la marca al pasar la
        cédula: se reutiliza para que ambos lados hablen del mismo evento. Solo
        si la BD estaba caída se genera aquí."""
        with self._lock:
            ahora = _ahora()
            id_evento = id_evento or uuid4().hex   # UUID v4 hex, 32 chars, sin guiones
            rut_fmt = formatea_rut(rut) or rut

            # Payload EXACTO que se enviará a la API (no se reconstruye en reintentos).
            payload = {
                "idEvento": id_evento,
                "idTerminal": config.ID_TERMINAL,
                "resultado": "AUTORIZADO" if autorizado else "RECHAZADO",
                "rut": rut_fmt,
                "evento": "ENTRADA" if sentido == "E" else "SALIDA",
                "fechaHora": ahora.isoformat(timespec="seconds"),
            }
            if autorizado:
                payload["nombre"] = (nombre or "")[:150]
            else:
                payload["motivoRechazo"] = (motivo or "Acceso no habilitado")[:250]
                # nombre es opcional en rechazadas: se envía SOLO si identificamos
                # a la persona (RUT conocido). Si es desconocido, se omite (contrato).
                nom = (nombre or "").strip()
                if nom and nom.lower() != "desconocido" and len(nom) >= 3:
                    payload["nombre"] = nom[:150]

            ev = {
                "id": self._next_id,
                "id_evento": id_evento,
                "timestamp": ahora.isoformat(timespec="seconds"),
                "fecha": ahora.strftime("%Y-%m-%d"),
                "hora": ahora.strftime("%H:%M:%S"),
                "centro": config.CENTRO,
                "reloj": config.RELOJ,
                "ubicacion": ubicacion,
                "rut": rut,                   # normalizado (rut_norm)
                "nombre": nombre,
                "sentido": sentido,           # 'E' o 'S'
                "codigo": codigo,             # 0-4
                "autorizado": bool(autorizado),
                "motivo": motivo,
                "payload": payload,           # se envía tal cual (idempotencia)
                "id_marca_local": id_marca_local,  # IdMarca en la BD local (SQL Server)
                "idMarca": None,              # lo devuelve la API al guardar
                "subido_local": 1 if id_marca_local else 0,
                "subido_api": 0,              # 0 pendiente · 1 subido · -1 fallido (no reintentar)
            }
            self._next_id += 1
            self.registros.append(ev)
            self._guardar()
            return dict(ev)

    def pendientes(self):
        with self._lock:
            return [dict(r) for r in self.registros
                    if not r.get("subido_local") or not r.get("subido_api")]

    def marcar(self, rid, local=None, api=None, extra=None):
        """Actualiza banderas y campos extra de una marca.
        `api` acepta True (1=ok), False (0=pendiente) o un entero (-1=fallido)."""
        with self._lock:
            for r in self.registros:
                if r.get("id") == rid:
                    if local is not None:
                        r["subido_local"] = 1 if local else 0
                    if api is not None:
                        r["subido_api"] = (1 if api else 0) if isinstance(api, bool) else int(api)
                    if extra:
                        r.update(extra)
                    self._guardar()
                    return True
        return False

    def resumen(self):
        with self._lock:
            total = len(self.registros)
            pend_local = sum(1 for r in self.registros if not r.get("subido_local"))
            pend_api = sum(1 for r in self.registros if not r.get("subido_api"))
            return {"total": total, "pend_local": pend_local, "pend_api": pend_api}
