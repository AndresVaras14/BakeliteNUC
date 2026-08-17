# -*- coding: utf-8 -*-
"""
Ajustes de hardware que el operador puede cambiar en caliente y que persisten
entre reinicios (ajustes.json).

Sirven para corregir montajes al revés SIN tocar el cableado ni el código:
  - invertir_lectoras: intercambia el sentido de Lectora 1 y Lectora 2
    (la que estaba como ENTRADA pasa a SALIDA y viceversa).
  - invertir_reles: intercambia qué relé se dispara para ENTRADA y SALIDA
    (por si el relé de entrada abre el torniquete de salida).

Los dos son independientes: uno arregla las lectoras, el otro el cableado
de los relés.
"""

import json
import logging

import config

log = logging.getLogger("ajustes")
RUTA = "ajustes.json"


class Ajustes:
    def __init__(self, ruta=RUTA):
        self.ruta = ruta
        self.invertir_lectoras = False
        self.invertir_reles = False
        self.cargar()

    def cargar(self):
        try:
            with open(self.ruta, "r", encoding="utf-8") as f:
                d = json.load(f)
            self.invertir_lectoras = bool(d.get("invertir_lectoras", False))
            self.invertir_reles = bool(d.get("invertir_reles", False))
            log.info("Ajustes cargados: lectoras=%s reles=%s",
                     self.invertir_lectoras, self.invertir_reles)
        except FileNotFoundError:
            log.info("Sin ajustes.json previo; usando valores por defecto.")
        except Exception as e:  # noqa: BLE001
            log.error("No se pudo leer %s: %s", self.ruta, e)

    def guardar(self):
        try:
            with open(self.ruta, "w", encoding="utf-8") as f:
                json.dump({
                    "invertir_lectoras": self.invertir_lectoras,
                    "invertir_reles": self.invertir_reles,
                }, f, indent=2, ensure_ascii=False)
            log.info("Ajustes guardados: lectoras=%s reles=%s",
                     self.invertir_lectoras, self.invertir_reles)
        except Exception as e:  # noqa: BLE001
            log.error("No se pudo guardar %s: %s", self.ruta, e)

    # ---- Lógica derivada ----
    def sentido_efectivo(self, nominal):
        """Sentido real considerando la inversión de lectoras."""
        if self.invertir_lectoras:
            return "S" if nominal == "E" else "E"
        return nominal

    def comando_rele(self, sentido):
        """Comando de relé para un sentido lógico, considerando la inversión.
        Normal: ENTRADA -> RELE1 (R2*), SALIDA -> RELE2 (R1*)."""
        if sentido == "E":
            return config.RELE2 if self.invertir_reles else config.RELE1
        return config.RELE1 if self.invertir_reles else config.RELE2
