import os
import sys
import unittest
from unittest.mock import MagicMock, patch

PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROYECTO not in sys.path:
    sys.path.insert(0, PROYECTO)

import config  # noqa: E402
import urllib.error  # noqa: E402
from sincronizacion_componentes.estado_api import EstadoApiMixin  # noqa: E402


class BdLocalFalsa:
    """Doble de la BD local: solo registra lo que se le pide."""

    def __init__(self):
        self.servicios = {}
        self.incidentes_abiertos = {}
        self.errores = []
        self._siguiente_id_incidente = 1

    def marcar_servicio(self, nombre, en_linea, error=None):
        self.servicios[nombre] = (en_linea, error)

    def abrir_incidente(self, servicio, error=None):
        if servicio not in self.incidentes_abiertos:
            self.incidentes_abiertos[servicio] = self._siguiente_id_incidente
            self._siguiente_id_incidente += 1
        return self.incidentes_abiertos[servicio]

    def cerrar_incidente(self, servicio):
        id_inc = self.incidentes_abiertos.pop(servicio, None)
        if id_inc is None:
            return None
        return {
            "id": id_inc, "duracion_segundos": 42, "intentos_fallidos": 3,
            "deteccion": "2026-01-01T00:00:00", "recuperacion": "2026-01-01T00:00:42",
        }

    def registrar_error(self, origen, mensaje, nivel="INFO", detalle=None):
        self.errores.append((origen, mensaje, nivel, detalle))


def _respuesta(code, cuerpo):
    """Fabrica un objeto usable como `with urlopen(...) as resp:`."""
    resp = MagicMock()
    resp.getcode.return_value = code
    resp.read.return_value = cuerpo.encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


class SincronizadorFalso(EstadoApiMixin):
    def __init__(self):
        self.bd_local = BdLocalFalsa()
        self.en_linea = None
        self.ultima_conexion = None
        self._incidente_abierto = False
        self._ultimo_ping = 0.0
        self.on_estado = None


class ApiVivaTests(unittest.TestCase):
    def test_2xx_esta_viva(self):
        self.assertTrue(EstadoApiMixin._api_viva(200))
        self.assertTrue(EstadoApiMixin._api_viva(201))

    def test_404_no_esta_viva_ruta_no_publicada(self):
        self.assertFalse(EstadoApiMixin._api_viva(404))

    def test_5xx_408_429_no_estan_vivos(self):
        for codigo in (500, 502, 503, 408, 429):
            self.assertFalse(EstadoApiMixin._api_viva(codigo), f"código {codigo}")

    def test_rechazos_de_validacion_cuentan_como_viva(self):
        for codigo in (400, 401, 403, 422):
            self.assertTrue(EstadoApiMixin._api_viva(codigo), f"código {codigo}")


class EstadoBdTests(unittest.TestCase):
    def test_bd_ok_devuelve_true(self):
        self.assertTrue(EstadoApiMixin._estado_bd('{"baseDatos": "OK", "estado": "OK"}'))

    def test_bd_ok_sin_campo_estado_tambien_es_true(self):
        self.assertTrue(EstadoApiMixin._estado_bd('{"baseDatos": "OK"}'))

    def test_bd_erronea_devuelve_false(self):
        self.assertFalse(
            EstadoApiMixin._estado_bd('{"baseDatos": "ERROR", "estado": "DEGRADADO"}'))

    def test_cuerpo_no_json_devuelve_none(self):
        self.assertIsNone(EstadoApiMixin._estado_bd("no es json"))

    def test_json_sin_campos_relevantes_devuelve_none(self):
        self.assertIsNone(EstadoApiMixin._estado_bd('{"otraCosa": 1}'))


class PingTests(unittest.TestCase):
    def setUp(self):
        self.sync = SincronizadorFalso()
        self._parches = [
            patch.object(config, "SIMULAR_API", False),
            patch.object(config, "ENDPOINT_ESTADO_API", "https://api.test/api/terminal/health"),
            patch.object(config, "API_TIMEOUT_SEGUNDOS", 5),
            patch.object(config, "USAR_BD_LOCAL", True),
        ]
        for p in self._parches:
            p.start()
            self.addCleanup(p.stop)

    def test_200_con_bd_ok_marca_en_linea(self):
        with patch("urllib.request.urlopen", return_value=_respuesta(
                200, '{"estado":"OK","baseDatos":"OK"}')):
            self.sync._ping()
        self.assertTrue(self.sync.en_linea)
        self.assertEqual((True, None), self.sync.bd_local.servicios["BAKELITE"])

    def test_200_con_bd_caida_marca_sin_conexion(self):
        with patch("urllib.request.urlopen", return_value=_respuesta(
                200, '{"estado":"DEGRADADO","baseDatos":"ERROR"}')):
            self.sync._ping()
        self.assertFalse(self.sync.en_linea)

    def test_404_marca_sin_conexion_api_no_publicada(self):
        error = urllib.error.HTTPError("url", 404, "Not Found", {}, None)
        with patch("urllib.request.urlopen", side_effect=error):
            self.sync._ping()
        self.assertFalse(self.sync.en_linea)

    def test_400_de_validacion_cuenta_como_en_linea(self):
        error = urllib.error.HTTPError("url", 400, "Bad Request", {}, None)
        with patch("urllib.request.urlopen", side_effect=error):
            self.sync._ping()
        self.assertTrue(self.sync.en_linea)

    def test_timeout_o_error_de_red_marca_sin_conexion(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError("timeout")):
            self.sync._ping()
        self.assertFalse(self.sync.en_linea)

    def test_recuperacion_cierra_el_incidente_abierto(self):
        error = urllib.error.HTTPError("url", 500, "Error", {}, None)
        with patch("urllib.request.urlopen", side_effect=error):
            self.sync._ping()
        self.assertTrue(self.sync._incidente_abierto)

        with patch("urllib.request.urlopen", return_value=_respuesta(
                200, '{"estado":"OK","baseDatos":"OK"}')):
            self.sync._ping()
        self.assertFalse(self.sync._incidente_abierto)
        self.assertNotIn("BAKELITE", self.sync.bd_local.incidentes_abiertos)


if __name__ == "__main__":
    unittest.main()
