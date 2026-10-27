# -*- coding: utf-8 -*-
"""Clave que identifica a este terminal ante /api/terminal/* de Bakelite.

Sin esta clave, la API devuelve 401 a todo lo que suba el torniquete: marcas,
heartbeat, incidentes, sincronización de dispositivos y de nombre.

Dos formas de obtenerla:

  1. Autoaprovisionamiento (lo normal para un torniquete nuevo): si todavía
     no existe clave_terminal.json pero sí clave_aprovisionamiento.json (la
     clave compartida que entrega quien instala equipos nuevos), este módulo
     llama solo UNA vez a POST /api/terminal/enroll con el número de
     config.ID_TERMINAL. Bakelite crea el terminal de cero (API + BD) y
     devuelve una clave propia, que se guarda en clave_terminal.json para
     todas las corridas siguientes.
  2. Manual: un admin con sesión en BakeliteWeb dispara
     POST /api/terminals/{idTerminal}/apikey/rotate y pega el resultado en
     clave_terminal.json a mano -- es lo que toca si el terminal YA existe en
     Bakelite (el autoaprovisionamiento rechaza re-enrolar uno existente, a
     propósito: evita que la clave compartida le robe la identidad a un
     torniquete que ya está operando).

Ambos archivos son locales y están en .gitignore.
"""

import json
import logging
import urllib.error
import urllib.request

import config
import identidad_terminal

log = logging.getLogger("terminal_auth")

HEADER = "X-Terminal-Key"

_clave = None


def _leer_json(ruta):
    with open(ruta, "r", encoding="utf-8") as f:
        return json.load(f)


def _guardar_clave(clave):
    with open(config.ARCHIVO_CLAVE_TERMINAL, "w", encoding="utf-8") as f:
        json.dump({"api_key": clave}, f, indent=2, ensure_ascii=False)


def _intentar_autoaprovisionar():
    """Sin clave_terminal.json todavía: si hay clave de aprovisionamiento,
    intenta crear el terminal de cero. Devuelve la clave nueva o None."""
    try:
        d = _leer_json(config.ARCHIVO_CLAVE_APROVISIONAMIENTO)
    except FileNotFoundError:
        return None
    except Exception as e:  # noqa: BLE001
        log.error("No se pudo leer %s: %s", config.ARCHIVO_CLAVE_APROVISIONAMIENTO, e)
        return None

    clave_aprov = str(d.get("clave_aprovisionamiento") or "")
    if not clave_aprov:
        log.error("%s existe pero no tiene clave_aprovisionamiento.",
                  config.ARCHIVO_CLAVE_APROVISIONAMIENTO)
        return None

    payload = {"idTerminal": config.ID_TERMINAL}
    if identidad_terminal.NOMBRE:
        payload["nombre"] = identidad_terminal.NOMBRE
    if identidad_terminal.UBICACION:
        payload["ubicacion"] = identidad_terminal.UBICACION

    peticion = urllib.request.Request(
        config.ENDPOINT_ENROLAR_TERMINAL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "X-Provisioning-Key": clave_aprov})
    try:
        with urllib.request.urlopen(peticion, timeout=config.API_TIMEOUT_SEGUNDOS) as resp:
            datos = json.loads(resp.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as e:
        cuerpo = e.read().decode("utf-8", "ignore")[:300]
        if e.code == 409:
            log.error(
                "El terminal %s ya existe en Bakelite; el autoaprovisionamiento "
                "no re-enrola uno existente. Pide a un admin que use "
                "POST /api/terminals/%s/apikey/rotate y pega el resultado en %s.",
                config.ID_TERMINAL, config.ID_TERMINAL, config.ARCHIVO_CLAVE_TERMINAL)
        else:
            log.error("Autoaprovisionamiento rechazado (HTTP %s): %s", e.code, cuerpo)
        return None
    except Exception as e:  # noqa: BLE001
        log.error("No se pudo autoaprovisionar el terminal %s: %s", config.ID_TERMINAL, e)
        return None

    clave = datos.get("apiKey")
    if not clave:
        log.error("Bakelite aceptó el autoaprovisionamiento pero no devolvió apiKey.")
        return None

    log.warning(
        "Terminal %s autoaprovisionado en Bakelite (API + BD creadas de cero). "
        "Clave guardada en %s.", config.ID_TERMINAL, config.ARCHIVO_CLAVE_TERMINAL)
    return clave


def _cargar():
    global _clave
    try:
        d = _leer_json(config.ARCHIVO_CLAVE_TERMINAL)
        _clave = str(d.get("api_key") or "") or None
    except FileNotFoundError:
        _clave = _intentar_autoaprovisionar()
        if _clave:
            try:
                _guardar_clave(_clave)
            except Exception as e:  # noqa: BLE001
                log.error("No se pudo guardar %s: %s (seguirá autoaprovisionando "
                         "en cada arranque hasta que se pueda escribir).",
                         config.ARCHIVO_CLAVE_TERMINAL, e)
    except Exception as e:  # noqa: BLE001
        log.error("No se pudo leer %s: %s", config.ARCHIVO_CLAVE_TERMINAL, e)
        _clave = None

    if not _clave:
        log.warning(
            "Sin clave de terminal en %s: BakeliteApi va a rechazar con 401 "
            "todo lo que suba este torniquete (marcas, heartbeat, dispositivos...).",
            config.ARCHIVO_CLAVE_TERMINAL)


_cargar()


def cabeceras(extra=None):
    """Headers para una petición a /api/terminal/*, con X-Terminal-Key agregado
    a lo que ya traiga `extra` (por ejemplo Content-Type)."""
    cab = dict(extra or {})
    if _clave:
        cab[HEADER] = _clave
    return cab
