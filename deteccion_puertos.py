# -*- coding: utf-8 -*-
"""
Detección y clasificación de puertos serie (§2 y §3).

Clasifica cada puerto por texto (udevadm en Linux, description+hwid en Windows):
    - Arduino  -> palabra 'arduino'
    - Lectora  -> honeywell / symbol / aigather / 1a86...

Asignación (§3):
    - primer Arduino -> 'arduino'
    - primera lectora libre -> 'lectora1', segunda -> 'lectora2'
    - nunca el mismo puerto en dos roles (anti-duplicado)

OJO (§2, §16.1): un Arduino clon con chip CH340 (1a86:7523) se clasifica como
lectora. Si eso ocurre, marca el Arduino con otro identificador o cámbialo por
uno con chip original.
"""

import glob
import subprocess
import logging

import config  # noqa: F401  (para futuros usos / consistencia)

try:
    from serial.tools import list_ports
except Exception:  # noqa: BLE001  # pragma: no cover
    list_ports = None

log = logging.getLogger("puertos")

PALABRAS_ARDUINO = ["arduino"]
PALABRAS_LECTORA = [
    "honeywell", "symbol", "aigather",
    "1a86_usb_barcode_scanner", "1a86_aigather_scan", "1a86",
]


def _identidad_linux(dev):
    try:
        out = subprocess.run(
            ["udevadm", "info", "--name", dev],
            capture_output=True, text=True, timeout=3,
        )
        return (out.stdout + out.stderr).lower()
    except Exception as e:  # noqa: BLE001
        log.debug("udevadm falló en %s: %s", dev, e)
        return dev.lower()


def _clasifica(texto):
    t = texto.lower()
    if any(w in t for w in PALABRAS_ARDUINO):
        return "arduino"
    if any(w in t for w in PALABRAS_LECTORA):
        return "lectora"
    return None


def detectar():
    """Devuelve {'arduino': ruta|None, 'lectora1': ruta|None, 'lectora2': ruta|None}."""
    resultado = {"arduino": None, "lectora1": None, "lectora2": None}
    candidatos = []  # [(dev, tipo)]

    devs = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    if devs:  # Linux
        for dev in devs:
            tipo = _clasifica(_identidad_linux(dev))
            if tipo:
                candidatos.append((dev, tipo))
    elif list_ports is not None:  # Windows / fallback
        for p in list_ports.comports():
            texto = f"{p.device} {p.description} {p.hwid}"
            tipo = _clasifica(texto)
            if tipo:
                candidatos.append((p.device, tipo))

    usados = set()

    for dev, tipo in candidatos:               # primer Arduino
        if tipo == "arduino" and dev not in usados:
            resultado["arduino"] = dev
            usados.add(dev)
            break

    slot = 1
    for dev, tipo in candidatos:               # primeras dos lectoras
        if tipo == "lectora" and dev not in usados and slot <= 2:
            resultado[f"lectora{slot}"] = dev
            usados.add(dev)
            slot += 1

    log.info("Detección de puertos: %s", resultado)
    return resultado
