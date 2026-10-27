import datetime
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
from sincronizacion_componentes.incidentes import IncidentesApiMixin  # noqa: E402


class BdLocalFalsa:
    def __init__(self, incidentes=None):
        self._incidentes = incidentes or []
        self.descartados = []
        self.avisos = []

    def incidentes_por_avisar(self):
        return list(self._incidentes)

    def descartar_incidente(self, id_incidente, motivo):
        self.descartados.append((id_incidente, motivo))

    def registrar_aviso_incidente(self, id_incidente, resultado, http_status=None,
                                   respuesta=None, id_registro_api=None,
                                   estado_api=None, error=None):
        self.avisos.append({
            "id": id_incidente, "resultado": resultado, "http_status": http_status,
            "id_registro_api": id_registro_api, "estado_api": estado_api, "error": error,
        })


def _respuesta(code, cuerpo):
    resp = MagicMock()
    resp.getcode.return_value = code
    resp.read.return_value = cuerpo.encode("utf-8")
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


class SincronizadorFalso(IncidentesApiMixin, EstadoApiMixin):
    """_enviar_incidente usa `self._leer`, definido en EstadoApiMixin."""

    def __init__(self, bd_local):
        self.bd_local = bd_local


def _incidente(**overrides):
    base = {
        "id": 1,
        "uuid": "77bb00d6222d46108707d352a9744128",
        "servicio": "BAKELITE",
        "deteccion": datetime.datetime(2026, 1, 1, 12, 0, 0),
        "recuperacion": datetime.datetime(2026, 1, 1, 12, 3, 16),
        "duracion_segundos": None,
        "intentos_fallidos": 18,
        "ultimo_error": "Timeout consultando la API",
    }
    base.update(overrides)
    return base


class PayloadIncidenteTests(unittest.TestCase):
    def setUp(self):
        self.sync = SincronizadorFalso(BdLocalFalsa())
        p = patch.object(config, "ID_TERMINAL", 1)
        p.start()
        self.addCleanup(p.stop)

    def test_arma_payload_completo_con_duracion_calculada(self):
        payload = self.sync._payload_incidente(_incidente())
        self.assertEqual(1, payload["idTerminal"])
        self.assertEqual("77bb00d6222d46108707d352a9744128", payload["idIncidente"])
        self.assertEqual("BAKELITE", payload["servicio"])
        self.assertEqual(196, payload["duracionSegundos"])
        self.assertEqual(18, payload["intentosFallidos"])
        self.assertEqual("Timeout consultando la API", payload["detalle"])

    def test_usa_duracion_ya_calculada_si_viene(self):
        payload = self.sync._payload_incidente(_incidente(duracion_segundos=999))
        self.assertEqual(999, payload["duracionSegundos"])

    def test_corte_menor_a_un_segundo_se_descarta(self):
        inc = _incidente(recuperacion=datetime.datetime(2026, 1, 1, 12, 0, 0))
        payload = self.sync._payload_incidente(inc)
        self.assertIsNone(payload)
        self.assertEqual(1, len(self.sync.bd_local.descartados))
        self.assertEqual(1, self.sync.bd_local.descartados[0][0])

    def test_sin_uuid_se_descarta(self):
        payload = self.sync._payload_incidente(_incidente(uuid=None))
        self.assertIsNone(payload)
        self.assertEqual(1, len(self.sync.bd_local.descartados))

    def test_intentos_fallidos_minimo_uno(self):
        payload = self.sync._payload_incidente(_incidente(intentos_fallidos=0))
        self.assertEqual(1, payload["intentosFallidos"])

    def test_detalle_ausente_se_envia_como_none(self):
        payload = self.sync._payload_incidente(_incidente(ultimo_error=None))
        self.assertIsNone(payload["detalle"])


class EnviarIncidenteTests(unittest.TestCase):
    def setUp(self):
        self.bd_local = BdLocalFalsa()
        self.sync = SincronizadorFalso(self.bd_local)
        p = patch.object(config, "ENDPOINT_REGISTRAR_INCIDENTE",
                          "https://api.test/api/terminal/incidents")
        p.start()
        self.addCleanup(p.stop)
        p2 = patch.object(config, "API_TIMEOUT_SEGUNDOS", 5)
        p2.start()
        self.addCleanup(p2.stop)

    def test_201_registrado_se_marca_ok(self):
        with patch("urllib.request.urlopen", return_value=_respuesta(
                201, '{"idRegistro": 42, "estado": "REGISTRADO"}')):
            resultado = self.sync._enviar_incidente(
                _incidente(), self.sync._payload_incidente(_incidente()))
        self.assertTrue(resultado)
        aviso = self.bd_local.avisos[0]
        self.assertEqual("OK", aviso["resultado"])
        self.assertEqual(42, aviso["id_registro_api"])
        self.assertEqual("REGISTRADO", aviso["estado_api"])

    def test_200_duplicado_tambien_se_marca_ok(self):
        with patch("urllib.request.urlopen", return_value=_respuesta(
                200, '{"idRegistro": 42, "estado": "DUPLICADO"}')):
            resultado = self.sync._enviar_incidente(
                _incidente(), self.sync._payload_incidente(_incidente()))
        self.assertTrue(resultado)
        self.assertEqual("DUPLICADO", self.bd_local.avisos[0]["estado_api"])

    def test_400_se_marca_permanente_no_reintenta(self):
        error = urllib.error.HTTPError(
            "url", 400, "Bad Request", {},
            _bytes_io('{"error": "fecha inválida"}'))
        with patch("urllib.request.urlopen", side_effect=error):
            resultado = self.sync._enviar_incidente(
                _incidente(), self.sync._payload_incidente(_incidente()))
        self.assertTrue(resultado)  # False solo significa "sin red"
        self.assertEqual("PERMANENTE", self.bd_local.avisos[0]["resultado"])

    def test_5xx_se_marca_reintentar(self):
        error = urllib.error.HTTPError("url", 503, "Unavailable", {}, _bytes_io(""))
        with patch("urllib.request.urlopen", side_effect=error):
            resultado = self.sync._enviar_incidente(
                _incidente(), self.sync._payload_incidente(_incidente()))
        self.assertTrue(resultado)
        self.assertEqual("REINTENTAR", self.bd_local.avisos[0]["resultado"])

    def test_fallo_de_red_devuelve_false_y_corta_el_ciclo(self):
        with patch("urllib.request.urlopen", side_effect=OSError("sin red")):
            resultado = self.sync._enviar_incidente(
                _incidente(), self.sync._payload_incidente(_incidente()))
        self.assertFalse(resultado)
        self.assertEqual("REINTENTAR", self.bd_local.avisos[0]["resultado"])

    def test_avisar_incidentes_corta_al_primer_fallo_de_red(self):
        self.bd_local._incidentes = [_incidente(id=1), _incidente(id=2)]
        p = patch.object(config, "USAR_BD_LOCAL", True)
        p.start()
        self.addCleanup(p.stop)
        with patch("urllib.request.urlopen", side_effect=OSError("sin red")):
            self.sync._avisar_incidentes()
        # Solo el primero debió intentarse: el ciclo se corta sin red.
        self.assertEqual(1, len(self.bd_local.avisos))


def _bytes_io(texto):
    import io
    return io.BytesIO(texto.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
