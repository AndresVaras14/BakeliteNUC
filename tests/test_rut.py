import os
import sys
import unittest

PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROYECTO not in sys.path:
    sys.path.insert(0, PROYECTO)

from rut import formatea_rut, normaliza_rut  # noqa: E402


class FormatoVisualRutTests(unittest.TestCase):
    def test_rut_menor_a_diez_millones_no_muestra_cero_inicial(self):
        self.assertEqual("4.266.307-7", formatea_rut("042663077"))

    def test_acepta_valor_ya_formateado_sin_conservar_cero(self):
        self.assertEqual("4.266.307-7", formatea_rut("04.266.307-7"))

    def test_rut_mayor_a_diez_millones_conserva_su_cuerpo(self):
        self.assertEqual("12.329.308-8", formatea_rut("123293088"))

    def test_normalizacion_interna_sigue_guardando_nueve_caracteres(self):
        self.assertEqual("042663077", normaliza_rut("4.266.307-7"))


if __name__ == "__main__":
    unittest.main()
