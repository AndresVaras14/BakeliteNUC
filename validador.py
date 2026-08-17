# -*- coding: utf-8 -*-
"""
Validación de acceso contra la base de pruebas (personas.json).

Implementa la máquina de estados de §7:
    código 0 -> NO habilitado            (rojo, sin relé)
    código 1 -> HABILITADO               (verde, pulso de relé)
    código 2 -> rechazo especial         (rojo)
    código 3 -> lectura inválida / RUT 0  (rojo)
    código 4 -> sin conexión a red        (amarillo)

En producción, esta clase es el único punto a reemplazar por la consulta real
a la BD / WebService, manteniendo la misma firma (validar -> Resultado).
"""

import json
import time
import logging
from dataclasses import dataclass

import config
from rut import fn_enmascara_rut, normaliza_rut, formatea_rut

log = logging.getLogger("validador")

MENSAJES = {
    0: "NO HABILITADO",
    1: "HABILITADO",
    2: "ERROR LECTURA / REINTENTE",
    3: "ERROR LECTURA / REINTENTE",
    4: "SIN CONEXIÓN A RED",
}


@dataclass
class Resultado:
    codigo: int
    mensaje: str
    autorizado: bool
    sentido: str            # 'E' o 'S'
    rut_norm: str = "0"
    rut_display: str = ""
    nombre: str = ""
    foto: str = None
    motivo: str = ""


class Validador:
    def __init__(self, ruta_json=None):
        self.ruta = ruta_json or config.ARCHIVO_PERSONAS
        self.por_rut = {}
        self.cargar()

    def cargar(self):
        try:
            with open(self.ruta, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:  # noqa: BLE001
            log.error("No se pudo cargar %s: %s", self.ruta, e)
            data = {}
        self.por_rut = {}
        for p in data.get("personas", []):
            clave = normaliza_rut(p.get("rut", ""))
            if clave != "0":
                self.por_rut[clave] = p
        log.info("Base de pruebas: %d personas cargadas desde %s",
                 len(self.por_rut), self.ruta)

    def validar_trama(self, trama, sentido, simular_sin_conexion=False):
        """Recibe la trama cruda de la lectora y devuelve un Resultado."""
        rut_norm = fn_enmascara_rut(trama)
        return self.validar(rut_norm, sentido, simular_sin_conexion)

    def validar(self, rut_norm, sentido, simular_sin_conexion=False):
        # Simula la latencia de la consulta. Durante este tiempo la luz azul
        # se mantiene encendida (relé + pantalla), tal como pidió el requisito.
        if config.VALIDACION_DELAY_SIMULADO > 0:
            time.sleep(config.VALIDACION_DELAY_SIMULADO)

        if simular_sin_conexion:
            return Resultado(4, MENSAJES[4], False, sentido, rut_norm)

        if rut_norm == "0":
            return Resultado(3, MENSAJES[3], False, sentido, rut_norm)

        persona = self.por_rut.get(rut_norm)
        if persona is None:
            return Resultado(
                0, MENSAJES[0], False, sentido, rut_norm,
                rut_display=formatea_rut(rut_norm),
                nombre="Desconocido",
                motivo="RUT no registrado",
            )

        habilitado = bool(persona.get("habilitado", False))
        codigo = 1 if habilitado else 0
        return Resultado(
            codigo, MENSAJES[codigo], habilitado, sentido, rut_norm,
            rut_display=persona.get("rut", formatea_rut(rut_norm)),
            nombre=persona.get("nombre", ""),
            foto=persona.get("foto"),
            motivo="" if habilitado else persona.get("motivo", "Acceso no habilitado"),
        )
