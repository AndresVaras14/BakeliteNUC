import os
import sys
import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROYECTO not in sys.path:
    sys.path.insert(0, PROYECTO)

import config  # noqa: E402
import urllib.error  # noqa: E402
from validador_api_externa import ValidadorApiExterna  # noqa: E402


def _respuesta(cuerpo_dict):
    """Fabrica un objeto usable como `with urlopen(...) as resp:`."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(cuerpo_dict).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _http_error(code):
    return urllib.error.HTTPError("url", code, "error", {}, None)


class ValidadorApiExternaTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({"user": "u", "passwd": "p", "rut_principal": "77.195.620-3"},
                  self._tmp)
        self._tmp.close()
        self.addCleanup(os.unlink, self._tmp.name)
        self.v = ValidadorApiExterna(ruta_credenciales=self._tmp.name)

    def test_credenciales_cargadas_disponible(self):
        self.assertTrue(self.v.disponible())
        self.assertIsNone(self.v.error_carga)

    def test_sin_credenciales_no_disponible(self):
        vacio = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8")
        json.dump({}, vacio)
        vacio.close()
        self.addCleanup(os.unlink, vacio.name)
        v = ValidadorApiExterna(ruta_credenciales=vacio.name)
        self.assertFalse(v.disponible())
        self.assertIsNotNone(v.error_carga)

    def test_rut_cero_no_llama_a_la_red(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            r = self.v.validar("0", "E")
        mock_urlopen.assert_not_called()
        self.assertEqual(3, r.codigo)

    def test_simular_sin_conexion_no_llama_a_la_red(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            r = self.v.validar("174542929", "E", simular_sin_conexion=True)
        mock_urlopen.assert_not_called()
        self.assertEqual(4, r.codigo)

    def test_habilitado_hace_login_y_consulta(self):
        login = _respuesta({"status": True, "cliente": 1, "api_key": "k1"})
        consulta = _respuesta({"rut": "17.454.292-9",
                               "nombre": "MARIO PEZO", "estado": 1, "motivo": ""})
        with patch("urllib.request.urlopen", side_effect=[login, consulta]):
            r = self.v.validar("174542929", "E")
        self.assertEqual(1, r.codigo)
        self.assertTrue(r.autorizado)
        self.assertEqual("MARIO PEZO", r.nombre)
        self.assertEqual("k1", self.v._api_key)

    def test_no_habilitado_trae_motivo(self):
        login = _respuesta({"status": True, "cliente": 1, "api_key": "k1"})
        consulta = _respuesta({"rut": "17.454.292-9", "nombre": "MARIO PEZO",
                               "estado": 0, "motivo": "Documentación incompleta."})
        with patch("urllib.request.urlopen", side_effect=[login, consulta]):
            r = self.v.validar("174542929", "E")
        self.assertEqual(0, r.codigo)
        self.assertFalse(r.autorizado)
        self.assertEqual("Documentación incompleta.", r.motivo)

    def test_api_key_se_reusa_en_la_siguiente_consulta(self):
        login = _respuesta({"status": True, "cliente": 1, "api_key": "k1"})
        c1 = _respuesta({"nombre": "A", "estado": 1, "motivo": ""})
        c2 = _respuesta({"nombre": "B", "estado": 1, "motivo": ""})
        with patch("urllib.request.urlopen",
                   side_effect=[login, c1, c2]) as mock_urlopen:
            self.v.validar("111111111", "E")
            self.v.validar("222222222", "S")
        self.assertEqual(3, mock_urlopen.call_count)   # 1 login + 2 consultas

    def test_401_renueva_api_key_y_reintenta(self):
        self.v._api_key = "vencido"
        login_nuevo = _respuesta({"status": True, "cliente": 1, "api_key": "k2"})
        consulta_ok = _respuesta({"nombre": "C", "estado": 1, "motivo": ""})
        with patch("urllib.request.urlopen",
                   side_effect=[_http_error(401), login_nuevo, consulta_ok]):
            r = self.v.validar("111111111", "E")
        self.assertEqual(1, r.codigo)
        self.assertEqual("k2", self.v._api_key)

    def test_login_rechazado_devuelve_codigo_4(self):
        login_malo = _respuesta({"status": False})
        with patch("urllib.request.urlopen", return_value=login_malo):
            r = self.v.validar("111111111", "E")
        self.assertEqual(4, r.codigo)

    def test_error_de_red_devuelve_codigo_4(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timeout")):
            r = self.v.validar("111111111", "E")
        self.assertEqual(4, r.codigo)


if __name__ == "__main__":
    unittest.main()
