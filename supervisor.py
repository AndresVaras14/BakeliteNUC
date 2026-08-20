# -*- coding: utf-8 -*-
"""
Supervisor: lanza la app y la vuelve a abrir si se cae.

Ejecutar ESTE archivo para un arranque a prueba de errores (en el equipo real):

    python3 supervisor.py

- Si main.py termina con error, lo relanza tras una pequeña espera.
- Si se cae muchas veces seguidas (crash-loop), espera más para no consumir CPU.
- Ctrl+C detiene el supervisor y la app.
- Cada caída queda registrada en logs/errores.log.
"""

import os
import sys
import time
import logging
import subprocess
from logging.handlers import RotatingFileHandler

import config

os.makedirs(config.DIR_LOGS, exist_ok=True)
log = logging.getLogger("supervisor")
log.setLevel(logging.INFO)
_h = RotatingFileHandler(config.ARCHIVO_LOG_ERRORES, maxBytes=1_000_000, backupCount=5,
                         encoding="utf-8")
_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
log.addHandler(_h)
log.addHandler(logging.StreamHandler())

MAIN = os.path.join(config.BASE_DIR, "main.py")
ESPERA_NORMAL = 3          # segundos entre reinicios
VENTANA_CRASHLOOP = 20     # si cae antes de estos segundos, cuenta como crash rápido
MAX_CRASHES_RAPIDOS = 5    # tras estos, espera larga
ESPERA_LARGA = 60


def main():
    crashes = 0
    log.info("Supervisor iniciado. Lanzando la app…")
    while True:
        inicio = time.time()
        try:
            ret = subprocess.call([sys.executable, MAIN], cwd=config.BASE_DIR)
        except KeyboardInterrupt:
            log.info("Supervisor detenido por el usuario.")
            return
        dur = time.time() - inicio

        if ret == 0:
            log.info("La app se cerró normalmente. Fin del supervisor.")
            return

        # Salida con error -> reiniciar
        if dur < VENTANA_CRASHLOOP:
            crashes += 1
        else:
            crashes = 0

        log.error("La app terminó con código %s tras %.1fs (caídas rápidas seguidas: %d).",
                  ret, dur, crashes)

        if crashes >= MAX_CRASHES_RAPIDOS:
            log.error("Demasiadas caídas seguidas; esperando %ss antes de reintentar.",
                      ESPERA_LARGA)
            time.sleep(ESPERA_LARGA)
            crashes = 0
        else:
            time.sleep(ESPERA_NORMAL)
        log.info("Relanzando la app…")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
