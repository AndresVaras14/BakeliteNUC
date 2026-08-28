# -*- coding: utf-8 -*-
"""
Detección y clasificación de puertos serie (§2 y §3).

Clasifica cada puerto por identidad USB y texto (udevadm en Linux,
description+hwid+VID/PID en Windows):
    - Arduino  -> VID/PID oficial conocido o palabra 'arduino'
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

# Arduino Uno oficial. Windows puede presentarlo solo como "Dispositivo serie
# USB", sin la palabra Arduino; VID/PID es la identidad confiable en ese caso.
VIDPID_ARDUINO = {
    (0x2341, 0x0043),  # Arduino Uno R3 (Arduino SA)
    (0x2341, 0x0001),  # Arduino Uno anterior
    (0x2A03, 0x0043),  # Arduino Uno R3 (Arduino SRL)
    (0x2A03, 0x0001),  # Arduino Uno anterior (Arduino SRL)
}
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


def _ancla(texto, dev):
    """Identificador estable de DÓNDE está enchufado el aparato.

    Las lectoras son CH340 idénticas y no traen número de serie: udev informa
    el mismo ID_SERIAL para las dos. Lo único que las distingue es el zócalo
    USB, que udev expone como ID_PATH y no cambia mientras no se muevan de
    puerto. El Arduino sí trae serie propia y se prefiere esa.
    """
    for clave in ("id_serial_short=", "id_path="):
        for linea in texto.splitlines():
            linea = linea.strip()
            pos = linea.find(clave)
            if pos >= 0:
                valor = linea[pos + len(clave):].strip()
                if valor:
                    return valor
    return dev


def _clasifica(texto, vid=None, pid=None):
    if vid is not None and pid is not None and (vid, pid) in VIDPID_ARDUINO:
        return "arduino"
    t = texto.lower()
    if any(w in t for w in PALABRAS_ARDUINO):
        return "arduino"
    if any(w in t for w in PALABRAS_LECTORA):
        return "lectora"
    return None


def detectar(anclas=None):
    """Devuelve {'arduino': ruta|None, 'lectora1': ruta|None, 'lectora2': ruta|None,
    'anclas': {slot: ancla}}.

    `anclas` es el mapa {slot: ancla} de la última vez, para que cada lectora
    conserve su número. Sin él, la asignación era por orden de aparición: al
    desenchufar la primera, la sobreviviente pasaba a ser 'lectora1' y el
    sistema informaba desconectada a la que seguía funcionando.

    Con las anclas, cada aparato vuelve a su lugar y el hueco queda donde de
    verdad se desenchufó algo.
    """
    resultado = {"arduino": None, "lectora1": None, "lectora2": None}
    candidatos = []  # [(dev, tipo, ancla)]

    devs = sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*"))
    if devs:  # Linux
        for dev in devs:
            texto = _identidad_linux(dev)
            tipo = _clasifica(texto)
            if tipo:
                candidatos.append((dev, tipo, _ancla(texto, dev)))
    elif list_ports is not None:  # Windows / fallback
        for p in list_ports.comports():
            texto = f"{p.device} {p.description} {p.hwid}"
            tipo = _clasifica(texto, p.vid, p.pid)
            if tipo:
                candidatos.append((p.device, tipo, p.hwid or p.device))

    anclas = dict(anclas or {})
    usados = set()

    for dev, tipo, _a in candidatos:           # primer Arduino
        if tipo == "arduino" and dev not in usados:
            resultado["arduino"] = dev
            usados.add(dev)
            break

    lectoras = [(dev, a) for dev, tipo, a in candidatos
                if tipo == "lectora" and dev not in usados]

    # 1) Cada lectora vuelve al número donde estaba enchufada.
    for slot in ("lectora1", "lectora2"):
        esperado = anclas.get(slot)
        if not esperado:
            continue
        for dev, ancla in lectoras:
            if ancla == esperado and dev not in usados:
                resultado[slot] = dev
                usados.add(dev)
                break

    # 2) Lo que sobra ocupa los números que hayan quedado libres.
    libres = [s for s in ("lectora1", "lectora2") if resultado[s] is None]
    for dev, ancla in lectoras:
        if dev in usados or not libres:
            continue
        resultado[libres.pop(0)] = dev
        usados.add(dev)

    # El ancla de cada una, para guardarla y volver a usarla la próxima vez.
    por_dev = {dev: ancla for dev, ancla in lectoras}
    resultado["anclas"] = {s: por_dev.get(resultado[s]) for s in ("lectora1", "lectora2")
                           if resultado[s]}

    log.info("Detección de puertos: %s", resultado)
    return resultado
