import glob
import json
import logging
import os
import sqlite3
import sys
import tempfile
import threading
import unittest

PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROYECTO not in sys.path:
    sys.path.insert(0, PROYECTO)

from bitacora import ManejadorBitacoraSQLite  # noqa: E402
from almacen_sqlite import conectar  # noqa: E402
from depurador import Depurador  # noqa: E402
from registros import RegistroStore  # noqa: E402


class RegistroStoreSQLiteTests(unittest.TestCase):
    def test_migra_json_actualiza_banderas_y_reabre(self):
        with tempfile.TemporaryDirectory(dir=PROYECTO) as temporal:
            ruta_db = os.path.join(temporal, "bakelite_nuc.db")
            ruta_json = os.path.join(temporal, "registros.json")
            evento = {
                "id": 7,
                "id_evento": "evento-legacy-1",
                "timestamp": "2026-08-28T10:00:00-04:00",
                "rut": "111111111",
                "nombre": "Prueba",
                "sentido": "E",
                "codigo": 1,
                "autorizado": True,
                "payload": {"idEvento": "evento-legacy-1"},
                "subido_local": 0,
                "subido_api": 0,
            }
            with open(ruta_json, "w", encoding="utf-8") as archivo:
                json.dump({"registros": [evento]}, archivo)

            store = RegistroStore(ruta_db, ruta_json)
            self.assertEqual(
                {"total": 1, "pend_local": 1, "pend_api": 1},
                store.resumen(),
            )
            self.assertFalse(os.path.exists(ruta_json))
            self.assertEqual(1, len(glob.glob(ruta_json + ".migrado-*.bak")))

            pendiente = store.pendientes()[0]
            self.assertEqual(7, pendiente["id"])
            self.assertTrue(store.marcar(7, local=True, api=True, extra={"idMarca": 91}))
            self.assertEqual([], store.pendientes())
            store.cerrar()

            reabierto = RegistroStore(ruta_db, ruta_json)
            self.assertEqual(1, reabierto.resumen()["total"])
            reabierto.cerrar()

    def test_registros_concurrentes_no_se_pierden(self):
        with tempfile.TemporaryDirectory(dir=PROYECTO) as temporal:
            store = RegistroStore(
                os.path.join(temporal, "bakelite_nuc.db"),
                os.path.join(temporal, "no-existe.json"),
            )

            def escribir(prefijo):
                for numero in range(25):
                    store.registrar(
                        "111111111", "Prueba", "E", 1, True,
                        id_evento=f"{prefijo}-{numero}",
                    )

            hilos = [threading.Thread(target=escribir, args=(f"h{n}",)) for n in range(4)]
            for hilo in hilos:
                hilo.start()
            for hilo in hilos:
                hilo.join()

            self.assertEqual(100, store.resumen()["total"])
            self.assertEqual(100, len(store.pendientes()))
            store.cerrar()

    def test_error_api_permanente_no_se_reintenta(self):
        with tempfile.TemporaryDirectory(dir=PROYECTO) as temporal:
            store = RegistroStore(
                os.path.join(temporal, "bakelite_nuc.db"),
                os.path.join(temporal, "no-existe.json"),
            )
            evento = store.registrar("111111111", "Prueba", "E", 1, True)
            store.marcar(evento["id"], local=True, api=-1,
                         extra={"api_error": "datos inválidos"})
            self.assertEqual([], store.pendientes())
            self.assertEqual(0, store.resumen()["pend_api"])
            store.cerrar()

    def test_base_corrupta_se_conserva_y_se_recrea(self):
        with tempfile.TemporaryDirectory(dir=PROYECTO) as temporal:
            ruta_db = os.path.join(temporal, "bakelite_nuc.db")
            with open(ruta_db, "wb") as archivo:
                archivo.write(b"esto no es sqlite")

            conexion = conectar(ruta_db)
            tabla = conexion.execute(
                "SELECT name FROM sqlite_master WHERE name = 'ColaEventos'"
            ).fetchone()
            conexion.close()

            self.assertIsNotNone(tabla)
            self.assertEqual(1, len(glob.glob(ruta_db + ".corrupto-*")))


class BitacoraSQLiteTests(unittest.TestCase):
    def test_guarda_contexto_y_redacta_secretos(self):
        with tempfile.TemporaryDirectory(dir=PROYECTO) as temporal:
            ruta_db = os.path.join(temporal, "bakelite_nuc.db")
            handler = ManejadorBitacoraSQLite(ruta_db)
            logger = logging.Logger("prueba-bitacora")
            logger.addHandler(handler)
            logger.warning(
                "Consulta fallida token=secreto",
                extra={"flujo": "→", "origen": "prueba", "datos": {"intento": 2}},
            )
            handler.close()

            conexion = sqlite3.connect(ruta_db)
            fila = conexion.execute(
                "SELECT Nivel, Logger, Mensaje, Flujo, Origen, DatosJson "
                "FROM BitacoraAplicacion"
            ).fetchone()
            conexion.close()
            self.assertEqual("WARNING", fila[0])
            self.assertEqual("prueba-bitacora", fila[1])
            self.assertEqual("Consulta fallida token=***", fila[2])
            self.assertEqual("→", fila[3])
            self.assertEqual("prueba", fila[4])
            self.assertIn('"intento": 2', fila[5])

    def test_acciones_del_depurador_entran_a_bitacora(self):
        with tempfile.TemporaryDirectory(dir=PROYECTO) as temporal:
            ruta_db = os.path.join(temporal, "bakelite_nuc.db")
            handler = ManejadorBitacoraSQLite(ruta_db)
            root = logging.getLogger()
            nivel_anterior = root.level
            root.setLevel(logging.DEBUG)
            root.addHandler(handler)
            try:
                Depurador(os.path.join(temporal, "debugger.log")).accion(
                    "Probar relé 1", origen="prueba",
                )
            finally:
                root.removeHandler(handler)
                root.setLevel(nivel_anterior)
                handler.close()

            conexion = sqlite3.connect(ruta_db)
            fila = conexion.execute(
                "SELECT Logger, Mensaje, Flujo, Origen FROM BitacoraAplicacion"
            ).fetchone()
            conexion.close()
            self.assertEqual(("flujo", "Probar relé 1", "→", "prueba"), fila)

    def test_cola_y_bitacora_escriben_juntas(self):
        with tempfile.TemporaryDirectory(dir=PROYECTO) as temporal:
            ruta_db = os.path.join(temporal, "bakelite_nuc.db")
            store = RegistroStore(
                ruta_db, os.path.join(temporal, "no-existe.json"))
            handler = ManejadorBitacoraSQLite(ruta_db)
            logger = logging.Logger("integracion")
            logger.addHandler(handler)

            for numero in range(20):
                store.registrar(
                    "111111111", "Prueba", "E", 1, True,
                    id_evento=f"integracion-{numero}",
                )
                logger.info("Evento de integración %d", numero)

            store.cerrar()
            handler.close()
            conexion = sqlite3.connect(ruta_db)
            eventos = conexion.execute("SELECT COUNT(*) FROM ColaEventos").fetchone()[0]
            entradas = conexion.execute(
                "SELECT COUNT(*) FROM BitacoraAplicacion WHERE Logger = 'integracion'"
            ).fetchone()[0]
            conexion.close()
            self.assertEqual(20, eventos)
            self.assertEqual(20, entradas)


if __name__ == "__main__":
    unittest.main()
