import os
import sys
import json
import tempfile
import unittest
import importlib
from unittest.mock import MagicMock, patch

PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROYECTO not in sys.path:
    sys.path.insert(0, PROYECTO)

import config  # noqa: E402
import urllib.error  # noqa: E402

RUTA_INEXISTENTE = os.path.join(tempfile.gettempdir(), "no-existe-nunca-bakelite-test.json")


def _recargar(ruta_clave, ruta_aprovisionamiento=RUTA_INEXISTENTE):
    """terminal_auth lee los archivos al importarse; para probar distintos
    escenarios hay que recargarlo con config apuntando a archivos de prueba."""
    with patch.object(config, "ARCHIVO_CLAVE_TERMINAL", ruta_clave), \
         patch.object(config, "ARCHIVO_CLAVE_APROVISIONAMIENTO", ruta_aprovisionamiento):
        import terminal_auth
        importlib.reload(terminal_auth)
        return terminal_auth


def _archivo_con(contenido):
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8")
    json.dump(contenido, tmp)
    tmp.close()
    return tmp.name


def _respuesta(cuerpo_dict, code=201):
    resp = MagicMock()
    resp.getcode.return_value = code
    resp.read.return_value = json.dumps(cuerpo_dict).encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


class TerminalAuthConClaveTests(unittest.TestCase):
    def test_con_clave_agrega_el_header(self):
        ruta = _archivo_con({"api_key": "abc123"})
        self.addCleanup(os.unlink, ruta)
        ta = _recargar(ruta)
        self.assertEqual({"X-Terminal-Key": "abc123"}, ta.cabeceras())

    def test_con_clave_conserva_los_headers_extra(self):
        ruta = _archivo_con({"api_key": "abc123"})
        self.addCleanup(os.unlink, ruta)
        ta = _recargar(ruta)
        self.assertEqual(
            {"Content-Type": "application/json", "X-Terminal-Key": "abc123"},
            ta.cabeceras({"Content-Type": "application/json"}))

    def test_archivo_sin_api_key_no_agrega_header(self):
        ruta = _archivo_con({})
        self.addCleanup(os.unlink, ruta)
        ta = _recargar(ruta)
        self.assertEqual({}, ta.cabeceras())


class TerminalAuthSinArchivosTests(unittest.TestCase):
    def test_sin_clave_ni_aprovisionamiento_no_agrega_header(self):
        ta = _recargar(RUTA_INEXISTENTE, RUTA_INEXISTENTE)
        self.assertEqual({}, ta.cabeceras())
        self.assertEqual({"Content-Type": "application/json"},
                         ta.cabeceras({"Content-Type": "application/json"}))


class TerminalAuthAutoaprovisionamientoTests(unittest.TestCase):
    def setUp(self):
        self.ruta_clave = os.path.join(tempfile.mkdtemp(), "clave_terminal.json")
        self.ruta_aprov = _archivo_con({"clave_aprovisionamiento": "secreto"})
        self.addCleanup(os.unlink, self.ruta_aprov)

    def test_enrola_y_guarda_la_clave_nueva(self):
        with patch("urllib.request.urlopen",
                   return_value=_respuesta({"idTerminal": 1, "apiKey": "clave-nueva"})):
            ta = _recargar(self.ruta_clave, self.ruta_aprov)

        self.assertEqual({"X-Terminal-Key": "clave-nueva"}, ta.cabeceras())
        with open(self.ruta_clave, "r", encoding="utf-8") as f:
            self.assertEqual("clave-nueva", json.load(f)["api_key"])

    def test_409_no_guarda_nada_y_pide_rotar_a_mano(self):
        error = urllib.error.HTTPError(
            "url", 409, "Conflict", {}, None)
        error.read = lambda: b'{"detail":"ya existe"}'
        with patch("urllib.request.urlopen", side_effect=error):
            ta = _recargar(self.ruta_clave, self.ruta_aprov)

        self.assertEqual({}, ta.cabeceras())
        self.assertFalse(os.path.exists(self.ruta_clave))

    def test_sin_clave_aprovisionamiento_no_intenta_la_red(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            ta = _recargar(self.ruta_clave, RUTA_INEXISTENTE)
        mock_urlopen.assert_not_called()
        self.assertEqual({}, ta.cabeceras())


if __name__ == "__main__":
    unittest.main()
