# -*- coding: utf-8 -*-
"""
Identidad de este equipo: su número de torniquete (idTerminal en Bakelite) y
la dirección de la API a la que reporta. Todo lo demás en config.py (las
URLs de cada endpoint) se arma a partir de estos dos valores -- es la única
configuración que de verdad cambia entre una instalación y otra.

Vive en identidad_terminal.json (gitignored, una copia distinta por equipo
instalado; ver identidad_terminal.example.json para el formato).

La dirección de la API se puede cambiar libremente (cambio de servidor,
ambiente de pruebas, etc.). El NÚMERO DE TORNIQUETE no: queda bloqueado en
numero_torniquete.bloqueado la primera vez que este equipo arranca con éxito.
Es el identificador con el que ya subió marcas y quedó dado de alta en
Bakelite -- si se cambiara por accidente en el JSON, las marcas siguientes se
atribuirían a OTRO torniquete y las de antes quedarían huérfanas. Cambiarlo
de verdad (reemplazo de hardware, reinstalación deliberada) exige borrar
también el archivo de bloqueo a mano; nunca basta con editar un solo campo
por error.

Si falta identidad_terminal.json (equipo recién clonado, entorno de pruebas)
se usan valores por defecto en vez de impedir el arranque -- el bloqueo del
número solo se vuelve estricto una vez que alguien lo definió de verdad.
"""

import os
import sys
import json
import logging

log = logging.getLogger("identidad_terminal")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ARCHIVO = os.path.join(_BASE_DIR, "identidad_terminal.json")
ARCHIVO_BLOQUEO = os.path.join(_BASE_DIR, "numero_torniquete.bloqueado")

NUMERO_TORNIQUETE_DEFECTO = 1
DIRECCION_API_DEFECTO = "https://bakeliteapi.sopytec.cl/"


def _cargar():
    try:
        with open(ARCHIVO, "r", encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        log.warning(
            "Sin %s; se usa el torniquete %s por defecto. Cópialo desde "
            "identidad_terminal.example.json antes de instalar de verdad.",
            ARCHIVO, NUMERO_TORNIQUETE_DEFECTO)
        d = {}
    except Exception as e:  # noqa: BLE001
        log.error("No se pudo leer %s: %s; se usan valores por defecto.", ARCHIVO, e)
        d = {}

    numero = d.get("numero_torniquete", NUMERO_TORNIQUETE_DEFECTO)
    if not isinstance(numero, int) or numero < 1:
        log.error("numero_torniquete inválido en %s (%r); se usa %s.",
                  ARCHIVO, numero, NUMERO_TORNIQUETE_DEFECTO)
        numero = NUMERO_TORNIQUETE_DEFECTO

    direccion = str(d.get("direccion_api") or DIRECCION_API_DEFECTO).strip()
    if not direccion.endswith("/"):
        direccion += "/"

    # Solo se usan una vez, al autoregistrar el terminal (ver terminal_auth.py);
    # Bakelite no vuelve a leerlos de aquí después.
    nombre = str(d.get("nombre") or "").strip() or None
    ubicacion = str(d.get("ubicacion") or "").strip() or None

    _verificar_bloqueo(numero)
    return numero, direccion, nombre, ubicacion


def _verificar_bloqueo(numero):
    """Primera vez que se ve un número: lo deja bloqueado. Las siguientes,
    exige que coincida -- si no, corta el arranque en vez de seguir con un
    número distinto al que este equipo ya usó para identificarse."""
    try:
        with open(ARCHIVO_BLOQUEO, "r", encoding="utf-8") as f:
            bloqueado = int(f.read().strip())
    except FileNotFoundError:
        try:
            with open(ARCHIVO_BLOQUEO, "w", encoding="utf-8") as f:
                f.write(str(numero))
            log.info("Número de torniquete %s bloqueado en %s.", numero, ARCHIVO_BLOQUEO)
        except Exception as e:  # noqa: BLE001
            log.error("No se pudo crear %s: %s (el número no quedó bloqueado).",
                      ARCHIVO_BLOQUEO, e)
        return
    except Exception as e:  # noqa: BLE001
        log.error("No se pudo leer %s: %s; no se verifica el bloqueo esta vez.",
                  ARCHIVO_BLOQUEO, e)
        return

    if numero != bloqueado:
        log.critical(
            "numero_torniquete cambió de %s a %s en %s, pero este equipo ya "
            "quedó dado de alta como el terminal %s. Si es un cambio "
            "deliberado (reinstalación, reemplazo de hardware), borra %s a "
            "mano para confirmarlo; si fue un error, vuelve a poner %s en el "
            "JSON.", bloqueado, numero, ARCHIVO, bloqueado, ARCHIVO_BLOQUEO, bloqueado)
        sys.exit(1)


NUMERO_TORNIQUETE, DIRECCION_API, NOMBRE, UBICACION = _cargar()
