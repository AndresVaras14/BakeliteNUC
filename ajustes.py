# -*- coding: utf-8 -*-
"""
Ajustes de hardware que el operador puede cambiar en caliente.

Qué lectora es ENTRADA y cuál SALIDA —y lo mismo para los relés— vive en la BD
local (dbo.Lectoras y dbo.Reles). Antes eran dos booleanos "invertir_*" en
ajustes.json: servían para corregir un montaje al revés, pero no decían cuál era
cuál, y se perdían si se reinstalaba el equipo.

ajustes.json sigue existiendo por dos motivos:
  - guarda la ubicación, que es solo de esta app;
  - guarda una copia del mapeo, para que el torniquete siga sabiendo qué relé
    abrir si arranca con la BD caída.

La BD manda siempre que esté disponible; el JSON es la red de seguridad.
"""

import json
import logging

import config

log = logging.getLogger("ajustes")

# Mapeo de fábrica, por si no hay BD ni JSON todavía (ver §6.1 de la
# especificación: el relé 1 abre la ENTRADA, a propósito "cruzado").
LECTORAS_DEFECTO = {1: config.SENTIDO_LECTORA1, 2: config.SENTIDO_LECTORA2}
RELES_DEFECTO = {1: "E", 2: "S"}
COMANDOS_DEFECTO = {1: config.RELE1, 2: config.RELE2}


class Ajustes:
    def __init__(self, ruta=None, bd_local=None):
        self.ruta = ruta or config.ARCHIVO_AJUSTES
        self.bd_local = bd_local
        self.ubicacion = config.UBICACION_DEFECTO
        self.lectoras = dict(LECTORAS_DEFECTO)     # numero -> 'E' | 'S'
        self.reles = dict(RELES_DEFECTO)           # numero -> 'E' | 'S'
        self.comandos = dict(COMANDOS_DEFECTO)     # numero -> 'R1*' | 'R2*'
        self.cargar()

    # ---- Carga y guardado ----
    def cargar(self):
        """JSON primero (siempre está), BD después (manda si contesta)."""
        self._cargar_json()
        self.cargar_de_bd()

    def _cargar_json(self):
        try:
            with open(self.ruta, "r", encoding="utf-8") as f:
                d = json.load(f)
        except FileNotFoundError:
            log.info("Sin ajustes.json previo; usando valores por defecto.")
            return
        except Exception as e:  # noqa: BLE001
            log.error("No se pudo leer %s: %s", self.ruta, e)
            return

        self.ubicacion = str(d.get("ubicacion", config.UBICACION_DEFECTO))
        for clave, destino in (("lectoras", self.lectoras), ("reles", self.reles)):
            guardado = d.get(clave) or {}
            for num, sentido in guardado.items():
                if sentido in ("E", "S"):
                    destino[int(num)] = sentido
        for num, cmd in (d.get("comandos") or {}).items():
            self.comandos[int(num)] = cmd

        # Compatibilidad con los ajustes viejos: si el equipo venía con las
        # lectoras o los relés invertidos, ese cruce se conserva al migrar.
        if d.get("invertir_lectoras"):
            self.lectoras = {n: ("S" if s == "E" else "E")
                             for n, s in LECTORAS_DEFECTO.items()}
            log.info("Migrado invertir_lectoras=True al mapeo explícito.")
        if d.get("invertir_reles"):
            self.reles = {1: "S", 2: "E"}
            log.info("Migrado invertir_reles=True al mapeo explícito.")

    def cargar_de_bd(self):
        """La BD es la fuente. Si no contesta, se queda lo que traía el JSON."""
        if self.bd_local is None:
            return False
        filas_l = self.bd_local.lectoras()
        filas_r = self.bd_local.reles()
        if not filas_l or not filas_r:
            log.warning("BD local sin configuración de lectoras/relés; se usa la del JSON.")
            return False
        self.lectoras = {f["numero"]: f["sentido"] for f in filas_l}
        self.reles = {f["numero"]: f["sentido"] for f in filas_r}
        self.comandos = {f["numero"]: f["comando"] for f in filas_r}
        log.info("Configuración cargada de la BD — lectoras: %s · relés: %s",
                 self.lectoras, self.reles)
        self._guardar_json()      # refresca la copia de respaldo
        return True

    def guardar(self):
        self._guardar_json()

    def _guardar_json(self):
        try:
            with open(self.ruta, "w", encoding="utf-8") as f:
                json.dump({
                    "ubicacion": self.ubicacion,
                    "lectoras": {str(n): s for n, s in self.lectoras.items()},
                    "reles": {str(n): s for n, s in self.reles.items()},
                    "comandos": {str(n): c for n, c in self.comandos.items()},
                }, f, indent=2, ensure_ascii=False)
        except Exception as e:  # noqa: BLE001
            log.error("No se pudo guardar %s: %s", self.ruta, e)

    # ---- Cambios (BD primero, memoria y JSON después) ----
    def set_sentido_lectora(self, numero, sentido, usuario=None):
        return self._set("lectoras", numero, sentido, usuario)

    def set_sentido_rele(self, numero, sentido, usuario=None):
        return self._set("reles", numero, sentido, usuario)

    def _set(self, cual, numero, sentido, usuario=None):
        """Solo hay dos aparatos de cada tipo: marcar uno como ENTRADA deja al
        otro como SALIDA. Se refleja igual en memoria aunque la BD esté caída,
        para que el torniquete siga operando con lo que el operador eligió."""
        if sentido not in ("E", "S"):
            log.error("Sentido inválido: %r", sentido)
            return False
        if self.bd_local is not None:
            fn = (self.bd_local.set_sentido_lectora if cual == "lectoras"
                  else self.bd_local.set_sentido_rele)
            if not fn(numero, sentido, usuario=usuario):
                log.warning("No se pudo guardar en la BD; queda solo en esta sesión.")
        destino = self.lectoras if cual == "lectoras" else self.reles
        contrario = "S" if sentido == "E" else "E"
        for n in destino:
            destino[n] = sentido if n == numero else contrario
        self._guardar_json()
        return True

    # ---- Lógica derivada ----
    def sentido_lectora(self, numero):
        """Sentido configurado para una lectora física."""
        return self.lectoras.get(numero, LECTORAS_DEFECTO.get(numero, "E"))

    def numero_lectora(self, sentido):
        """Qué lectora física cumple ese sentido. None si ninguna."""
        for n, s in self.lectoras.items():
            if s == sentido:
                return n
        return None

    def comando_rele(self, sentido):
        """Comando ASCII del relé que abre ese sentido."""
        for numero, s in self.reles.items():
            if s == sentido:
                return self.comandos.get(numero, COMANDOS_DEFECTO.get(numero))
        return COMANDOS_DEFECTO[1] if sentido == "E" else COMANDOS_DEFECTO[2]

    def numero_rele(self, sentido):
        """Qué relé físico abre ese sentido. None si ninguno."""
        for numero, s in self.reles.items():
            if s == sentido:
                return numero
        return None

    def comando_de_rele(self, numero):
        """Comando ASCII de un relé por su número (para probarlo)."""
        return self.comandos.get(numero, COMANDOS_DEFECTO.get(numero))

    def sentido_efectivo(self, nominal):
        """Compatibilidad: antes el sentido venía del config y se invertía. Hoy
        lo manda la configuración por número de lectora."""
        numero = 1 if nominal == config.SENTIDO_LECTORA1 else 2
        return self.sentido_lectora(numero)
