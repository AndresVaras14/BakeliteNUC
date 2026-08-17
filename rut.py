# -*- coding: utf-8 -*-
"""
Extracción y normalización del RUT chileno.
Réplica de `fnEnmascaraRut` (§8.2 de ESPECIFICACION_HARDWARE.md).

Formatos que emiten las lectoras de cédula:
  - PDF417 nuevo (URL):  ...?RUN=12345678-9&...
  - MRZ:                 contiene 'CHL' y el primer carácter es dígito.
"""


def normaliza_rut(rut_raw):
    """Deja solo dígitos y el DV 'K', y rellena a 9 caracteres con ceros.
    Devuelve '0' si no queda nada válido."""
    if not rut_raw:
        return "0"
    s = "".join(ch for ch in str(rut_raw).upper() if ch.isdigit() or ch == "K")
    if not s or s == "0":
        return "0"
    return s.rjust(9, "0")


def fn_enmascara_rut(trama):
    """Devuelve el RUT normalizado (9 caracteres) o '0' si no se reconoce
    el formato (lo que se traduce en código 3 = error de lectura)."""
    if not trama:
        return "0"
    t = str(trama)

    # Formato URL del PDF417 nuevo: ...?RUN=12345678-9&...
    if "?RUN=" in t:
        resto = t.split("?RUN=", 1)[1]
        valor = resto.split("&", 1)[0]
        return normaliza_rut(valor)

    # Formato MRZ: contiene 'CHL' y el primer carácter es dígito.
    if "CHL" in t and t[:1].isdigit():
        return normaliza_rut(t[:9])

    return "0"


def formatea_rut(norm):
    """Presenta un RUT normalizado como '4.266.307-7' para pantalla."""
    if not norm or norm == "0":
        return ""
    cuerpo = norm[:-1].lstrip("0") or "0"
    dv = norm[-1]
    rev = cuerpo[::-1]
    partes = [rev[i:i + 3] for i in range(0, len(rev), 3)]
    cuerpo_fmt = ".".join(partes)[::-1]
    return f"{cuerpo_fmt}-{dv}"
