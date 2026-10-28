import os
import sys
import tempfile
import datetime
import unittest

PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROYECTO not in sys.path:
    sys.path.insert(0, PROYECTO)

import config  # noqa: E402
from basedatos import BDLocal  # noqa: E402


class BDLocalSQLiteTests(unittest.TestCase):
    """BDLocal ya no es SQL Server (BakeliteTorniquete): corre sobre el mismo
    archivo SQLite que la cola y la bitácora. Estos tests cubren lo que antes
    garantizaba el esquema de SQL Server y ahora hay que probar a mano."""

    def setUp(self):
        self._ruta_original = config.ARCHIVO_SQLITE_LOCAL
        tmp = tempfile.mkdtemp()
        config.ARCHIVO_SQLITE_LOCAL = os.path.join(tmp, "bakelite_nuc.db")
        self.addCleanup(self._restaurar_ruta)
        self.bd = BDLocal()
        self.addCleanup(self.bd.cerrar)

    def _restaurar_ruta(self):
        config.ARCHIVO_SQLITE_LOCAL = self._ruta_original

    # ---- Terminal ----
    def test_terminal_se_crea_solo_al_pedirlo(self):
        t = self.bd.terminal()
        self.assertEqual(config.ID_TERMINAL, t["id"])
        self.assertTrue(t["nombre"])
        self.assertEqual("LOCAL", t["nombre_origen"])

    def test_renombrar_terminal_queda_pendiente_de_subir(self):
        r = self.bd.renombrar_terminal("Torniquete Norte", usuario="ana")
        self.assertEqual("Torniquete Norte", r["nombre"])
        self.assertFalse(r["nombre_sincronizado"])
        self.assertEqual("Torniquete Norte", self.bd.terminal()["nombre"])

    def test_aplicar_nombre_remoto_mas_nuevo_se_adopta(self):
        self.bd.renombrar_terminal("Nombre viejo", usuario="ana")
        futuro = (datetime.datetime.now().astimezone()
                  + datetime.timedelta(minutes=1)).isoformat(timespec="seconds")
        self.assertTrue(self.bd.aplicar_nombre_remoto("Nombre nuevo", futuro))
        self.assertEqual("Nombre nuevo", self.bd.terminal()["nombre"])

    def test_aplicar_nombre_remoto_mas_viejo_se_ignora(self):
        self.bd.renombrar_terminal("Nombre actual", usuario="ana")
        pasado = (datetime.datetime.now().astimezone()
                  - datetime.timedelta(minutes=1)).isoformat(timespec="seconds")
        self.assertFalse(self.bd.aplicar_nombre_remoto("Nombre viejo", pasado))
        self.assertEqual("Nombre actual", self.bd.terminal()["nombre"])

    # ---- Lectoras / relés ----
    def test_lectoras_y_reles_traen_el_mapeo_de_fabrica(self):
        lectoras = {l["numero"]: l["sentido"] for l in self.bd.lectoras()}
        reles = {r["numero"]: r["sentido"] for r in self.bd.reles()}
        self.assertEqual({1: "E", 2: "S"}, lectoras)
        self.assertEqual({1: "E", 2: "S"}, reles)

    def test_set_sentido_lectora_intercambia_las_dos(self):
        """El bug real que encontré: SQLite valida el índice único fila por
        fila (SQL Server lo hace al terminar la sentencia) -- un intercambio
        mal hecho choca contra el propio índice."""
        self.assertTrue(self.bd.set_sentido_lectora(2, "E", usuario="ana"))
        lectoras = {l["numero"]: l["sentido"] for l in self.bd.lectoras()}
        self.assertEqual({1: "S", 2: "E"}, lectoras)

    def test_set_sentido_rele_intercambia_las_dos(self):
        self.assertTrue(self.bd.set_sentido_rele(2, "E", usuario="ana"))
        reles = {r["numero"]: r["sentido"] for r in self.bd.reles()}
        self.assertEqual({1: "S", 2: "E"}, reles)

    def test_set_sentido_invalido_se_rechaza(self):
        self.assertFalse(self.bd.set_sentido_lectora(1, "N", usuario="ana"))

    def test_aplicar_config_remota_mas_nueva_intercambia_las_dos(self):
        futuro = (datetime.datetime.now().astimezone()
                  + datetime.timedelta(minutes=1)).isoformat(timespec="seconds")
        self.assertTrue(self.bd.aplicar_config_remota(
            "lectora", 2, "E", futuro, descripcion="Norte", config_por="web"))
        lectoras = {l["numero"]: l["sentido"] for l in self.bd.lectoras()}
        self.assertEqual({1: "S", 2: "E"}, lectoras)

    def test_aplicar_config_remota_mas_vieja_se_ignora(self):
        pasado = (datetime.datetime.now().astimezone()
                  - datetime.timedelta(minutes=1)).isoformat(timespec="seconds")
        self.assertFalse(self.bd.aplicar_config_remota("lectora", 2, "E", pasado))
        lectoras = {l["numero"]: l["sentido"] for l in self.bd.lectoras()}
        self.assertEqual({1: "E", 2: "S"}, lectoras)

    def test_marcar_dispositivos_sincronizados(self):
        self.bd.set_sentido_lectora(2, "E", usuario="ana")
        pendientes = self.bd.dispositivos_pendientes()
        self.assertEqual(2, len(pendientes["lectoras"]))
        self.assertTrue(self.bd.marcar_dispositivos_sincronizados("lectora", [1, 2]))
        pendientes = self.bd.dispositivos_pendientes()
        self.assertEqual(0, len(pendientes["lectoras"]))

    def test_estado_lectora_limpia_el_error_al_reconectar(self):
        self.bd.estado_lectora(1, conectada=False, error="Se desconectó del puerto")
        self.assertEqual("Se desconectó del puerto", self.bd.lectoras()[0]["ultimo_error"])
        self.bd.estado_lectora(1, conectada=True, puerto="COM3")
        self.assertIsNone(self.bd.lectoras()[0]["ultimo_error"])
        self.assertTrue(self.bd.lectoras()[0]["conectada"])

    def test_estado_arduino(self):
        self.assertTrue(self.bd.estado_arduino(True, puerto="COM5"))
        t = self.bd.terminal()
        self.assertTrue(t["arduino_conectado"])
        self.assertEqual("COM5", t["arduino_puerto"])

    # ---- Estado de servicios ----
    def test_marcar_servicio_en_linea_y_caido(self):
        self.assertTrue(self.bd.marcar_servicio("BAKELITE", True))
        self.assertTrue(self.bd.estado_servicio("BAKELITE")["en_linea"])
        self.assertTrue(self.bd.marcar_servicio("BAKELITE", False, error="timeout"))
        est = self.bd.estado_servicio("BAKELITE")
        self.assertFalse(est["en_linea"])
        self.assertEqual("timeout", est["ultimo_error"])

    # ---- Incidentes de conexión ----
    def test_abrir_y_cerrar_incidente(self):
        id1 = self.bd.abrir_incidente("EXTERNA", error="timeout")
        self.assertIsNotNone(id1)
        self.assertIsNotNone(self.bd.incidente_abierto("EXTERNA"))
        cerrado = self.bd.cerrar_incidente("EXTERNA")
        self.assertEqual(id1, cerrado["id"])
        self.assertIsNotNone(cerrado["duracion_segundos"])
        self.assertIsNone(self.bd.incidente_abierto("EXTERNA"))

    def test_abrir_incidente_repetido_no_duplica(self):
        id1 = self.bd.abrir_incidente("EXTERNA", error="e1")
        id2 = self.bd.abrir_incidente("EXTERNA", error="e2")
        self.assertEqual(id1, id2)
        self.assertEqual(2, self.bd.incidente_abierto("EXTERNA")["intentos_fallidos"])

    def test_incidentes_por_avisar_solo_los_recuperados_pendientes(self):
        self.bd.abrir_incidente("EXTERNA", error="e1")
        self.bd.cerrar_incidente("EXTERNA")
        pendientes = self.bd.incidentes_por_avisar()
        self.assertEqual(1, len(pendientes))
        self.assertTrue(self.bd.registrar_aviso_incidente(
            pendientes[0]["id"], "OK", http_status=201, id_registro_api=5,
            estado_api="REGISTRADO"))
        self.assertEqual([], self.bd.incidentes_por_avisar())

    # ---- Historial corto de marcas ----
    def test_flujo_completo_de_una_marca(self):
        m = self.bd.registrar_marca(
            rut="174542929", evento="E",
            fecha_hora="2026-10-27T10:00:00-03:00", rut_formateado="17.454.292-9")
        self.assertIsNotNone(m["id_marca"])
        self.assertTrue(self.bd.registrar_consulta_externa(
            rut_consultado="174542929", id_marca=m["id_marca"],
            habilitado=True, nombre="Mario Pezo", exito=True))
        self.assertEqual(1, self.bd.registrar_envio_bakelite(
            m["id_marca"], "OK", http_status=201, estado_api="REGISTRADO"))

        ultimas = self.bd.ultimas_marcas(5)
        self.assertEqual(1, len(ultimas))
        self.assertEqual("Mario Pezo", ultimas[0]["nombre"])
        self.assertEqual("AUTORIZADO", ultimas[0]["resultado"])
        self.assertEqual("ENVIADA", ultimas[0]["estado_envio"])

    def test_ultimas_marcas_se_poda_al_tope(self):
        from basedatos_componentes.marcas import TOPE_ULTIMAS_MARCAS
        for i in range(TOPE_ULTIMAS_MARCAS + 5):
            self.bd.registrar_marca(rut=str(i), evento="E", fecha_hora="2026-10-27T10:00:00")
        ultimas = self.bd.ultimas_marcas(100)
        self.assertEqual(TOPE_ULTIMAS_MARCAS, len(ultimas))
        # Se conservan las últimas, no las primeras.
        self.assertEqual(str(TOPE_ULTIMAS_MARCAS + 4), ultimas[0]["rut"])

    def test_id_marca_por_evento(self):
        m = self.bd.registrar_marca(rut="1", evento="E", fecha_hora="2026-10-27T10:00:00")
        self.assertEqual(m["id_marca"], self.bd.id_marca_por_evento(m["id_evento"]))
        self.assertIsNone(self.bd.id_marca_por_evento("no-existe"))

    def test_recuperar_marca_es_idempotente_por_id_evento(self):
        ev = {"id_evento": "abc123", "rut": "174542929", "sentido": "E",
              "timestamp": "2026-10-27T10:00:00", "payload": {"rut": "17.454.292-9"},
              "autorizado": True, "nombre": "Mario", "subido_api": 0}
        id1 = self.bd.recuperar_marca(ev)
        id2 = self.bd.recuperar_marca(ev)
        self.assertEqual(id1, id2)

    def test_registrar_error_no_rompe_aunque_no_haya_tabla_propia(self):
        self.assertTrue(self.bd.registrar_error("lectora", "algo pasó", nivel="WARN"))


if __name__ == "__main__":
    unittest.main()
