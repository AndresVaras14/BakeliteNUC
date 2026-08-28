#!/usr/bin/env python3
"""Instalador único: detecta Windows o Linux y ejecuta el instalador adecuado."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


def _argumentos(sistema: str) -> list[str]:
    argumentos = []
    for argumento in sys.argv[1:]:
        if argumento in {"--verificar", "/verificar"}:
            argumentos.append("/verificar" if sistema == "windows" else "--verificar")
        elif argumento == "--sin-bd" and sistema == "linux":
            argumentos.append(argumento)
        elif argumento in {"-h", "--help", "/?"}:
            print("Uso: python instalar.py [--verificar] [--sin-bd (solo Linux)]")
            raise SystemExit(0)
        else:
            raise SystemExit(f"Opción no reconocida para {sistema}: {argumento}")
    return argumentos


def main() -> int:
    sistema = platform.system().lower()
    entorno = os.environ.copy()
    # Los instaladores usan exactamente este intérprete, no un alias de Store
    # ni otro Python que aparezca antes en PATH.
    entorno["BAKELITE_PYTHON"] = sys.executable
    entorno["BAKELITE_NO_PAUSE"] = "1"

    if sistema == "windows":
        comando = [
            os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c",
            str(BASE_DIR / "instalar_windows.bat"),
            *_argumentos(sistema),
        ]
    elif sistema == "linux":
        comando = [
            "bash", str(BASE_DIR / "instalar_linux.sh"),
            *_argumentos(sistema),
        ]
    else:
        print(f"Sistema no soportado automáticamente: {platform.system()}",
              file=sys.stderr)
        return 2

    print(f"Sistema detectado: {platform.system()}")
    print(f"Python seleccionado: {sys.executable}")
    return subprocess.call(comando, cwd=BASE_DIR, env=entorno)


if __name__ == "__main__":
    raise SystemExit(main())
