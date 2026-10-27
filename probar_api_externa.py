# -*- coding: utf-8 -*-
"""
Prueba manual de la API externa INNTEK/ASEM, sin levantar la app completa.

Uso:
    python probar_api_externa.py 17.454.292-9
    python probar_api_externa.py 174542929

Lee las credenciales de credenciales_api_externa.json (igual que
ValidadorApiExterna), pide el RUT que se le pase como argumento y muestra el
Resultado tal cual lo vería el controlador.
"""

import sys

from rut import normaliza_rut
from validador_api_externa import ValidadorApiExterna


def main():
    if len(sys.argv) != 2:
        print("Uso: python probar_api_externa.py <rut>")
        sys.exit(1)

    v = ValidadorApiExterna()
    if not v.disponible():
        print(f"ERROR: {v.error_carga}")
        sys.exit(1)

    rut_norm = normaliza_rut(sys.argv[1])
    print(f"Consultando RUT {sys.argv[1]} (normalizado: {rut_norm}) ...")
    resultado = v.validar(rut_norm, "E")

    print(f"  código:    {resultado.codigo}")
    print(f"  mensaje:   {resultado.mensaje}")
    print(f"  autorizado:{resultado.autorizado}")
    print(f"  nombre:    {resultado.nombre}")
    print(f"  motivo:    {resultado.motivo}")


if __name__ == "__main__":
    main()
