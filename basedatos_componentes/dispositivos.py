# -*- coding: utf-8 -*-
"""Configuración y estado físico de lectoras, relés y Arduino (SQLite)."""

import logging

import config
from .comun import _ahora_iso, _bin, _corta, _leer_bool, _texto_fecha

log = logging.getLogger("basedatos")

# Mapeo de fábrica (ver ajustes.py: el relé 1 abre la ENTRADA, "cruzado" a
# propósito según §6.1 de ESPECIFICACION_HARDWARE.md).
_LECTORAS_DEFECTO = ((1, "E"), (2, "S"))
_RELES_DEFECTO = ((1, "E", "R2*"), (2, "S", "R1*"))


class DispositivosMixin:
    """Configuración y estado físico de lectoras, relés y Arduino."""

    def _asegurar_dispositivos(self, cur, idt):
        cur.execute("SELECT 1 FROM Lectoras WHERE IdTerminal = ? LIMIT 1", (idt,))
        if not cur.fetchone():
            ahora = _ahora_iso()
            for numero, sentido in _LECTORAS_DEFECTO:
                cur.execute(
                    "INSERT INTO Lectoras (IdTerminal, Numero, Sentido, Descripcion, ConfigFecha) "
                    "VALUES (?,?,?,?,?)",
                    (idt, numero, sentido, f"Lectora {numero}", ahora))
        cur.execute("SELECT 1 FROM Reles WHERE IdTerminal = ? LIMIT 1", (idt,))
        if not cur.fetchone():
            ahora = _ahora_iso()
            for numero, sentido, comando in _RELES_DEFECTO:
                cur.execute(
                    "INSERT INTO Reles (IdTerminal, Numero, Sentido, Comando, Descripcion, ConfigFecha) "
                    "VALUES (?,?,?,?,?,?)",
                    (idt, numero, sentido, comando, f"Relé {numero}", ahora))

    def lectoras(self, id_terminal=None):
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal

        def _fn(cur):
            self._asegurar_dispositivos(cur, idt)
            cur.execute(
                "SELECT Numero, Sentido, Descripcion, UltimoPuerto, Activa, "
                "       ConfigFecha, ConfigOrigen, ConfigPor, Sincronizado, "
                "       Conectada, UltimaLectura, UltimoError, Ancla "
                "FROM Lectoras WHERE IdTerminal = ? ORDER BY Numero", (idt,))
            return [{"numero": r[0], "sentido": r[1], "descripcion": r[2],
                     "puerto": r[3], "activa": bool(r[4]),
                     "config_fecha": r[5], "config_origen": r[6],
                     "config_por": r[7], "sincronizado": bool(r[8]),
                     "conectada": _leer_bool(r[9]),
                     "ultima_lectura": r[10], "ultimo_error": r[11], "ancla": r[12]}
                    for r in cur.fetchall()]

        ok, res = self._ejecutar(_fn, "lectoras")
        return res if ok else []

    def reles(self, id_terminal=None):
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal

        def _fn(cur):
            self._asegurar_dispositivos(cur, idt)
            cur.execute(
                "SELECT Numero, Sentido, Comando, Descripcion, Activo, "
                "       ConfigFecha, ConfigOrigen, ConfigPor, Sincronizado, "
                "       UltimoDisparo, UltimoError "
                "FROM Reles WHERE IdTerminal = ? ORDER BY Numero", (idt,))
            return [{"numero": r[0], "sentido": r[1], "comando": r[2],
                     "descripcion": r[3], "activo": bool(r[4]),
                     "config_fecha": r[5], "config_origen": r[6],
                     "config_por": r[7], "sincronizado": bool(r[8]),
                     "ultimo_disparo": r[9], "ultimo_error": r[10]}
                    for r in cur.fetchall()]

        ok, res = self._ejecutar(_fn, "reles")
        return res if ok else []

    # ---- Configuración (se sincroniza) ----
    def set_sentido_lectora(self, numero, sentido, usuario=None):
        """Marca una lectora como ENTRADA o SALIDA; la otra recibe el contrario."""
        return self._set_sentido("Lectoras", numero, sentido, usuario)

    def set_sentido_rele(self, numero, sentido, usuario=None):
        """Marca un relé como ENTRADA o SALIDA; el otro recibe el contrario."""
        return self._set_sentido("Reles", numero, sentido, usuario)

    def _set_sentido(self, tabla, numero, sentido, usuario=None, id_terminal=None):
        """El índice único garantiza una ENTRADA y una SALIDA por terminal, así
        que el cambio va en una sola sentencia con CASE: intercambia los dos a
        la vez y el motor valida el unique recién al terminarla. En dos UPDATE
        reventaría a mitad de camino.

        Sella ConfigFecha y deja Sincronizado = 0: el cambio queda pendiente de
        subir a Bakelite, y esa fecha es la que decide quién gana si allá
        también lo cambiaron.
        """
        if sentido not in ("E", "S"):
            log.error("Sentido inválido: %r (debe ser E o S).", sentido)
            return False
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal
        contrario = "S" if sentido == "E" else "E"

        def _fn(cur):
            self._asegurar_dispositivos(cur, idt)
            ahora = _ahora_iso()
            usr = _corta(usuario, 100)
            # SQLite valida el índice único de a una fila, a diferencia de SQL
            # Server (que lo hace recién al terminar la sentencia): un solo
            # UPDATE que intercambia los dos sentidos choca a mitad de camino.
            # 'X' saca al otro dispositivo del índice por un instante -- nunca
            # es visible fuera de esta transacción.
            cur.execute(f"UPDATE {tabla} SET Sentido = 'X' "
                        "WHERE IdTerminal = ? AND Numero != ?", (idt, numero))
            cur.execute(
                f"UPDATE {tabla} "
                "SET Sentido = ?, ConfigFecha = ?, ConfigOrigen = 'LOCAL', "
                "    ConfigPor = ?, Sincronizado = 0 "
                "WHERE IdTerminal = ? AND Numero = ?",
                (sentido, ahora, usr, idt, numero))
            afectadas = cur.rowcount
            cur.execute(
                f"UPDATE {tabla} "
                "SET Sentido = ?, ConfigFecha = ?, ConfigOrigen = 'LOCAL', "
                "    ConfigPor = ?, Sincronizado = 0 "
                "WHERE IdTerminal = ? AND Sentido = 'X'",
                (contrario, ahora, usr, idt))
            return afectadas > 0 and cur.rowcount > 0

        ok, res = self._ejecutar(_fn, f"set_sentido {tabla}")
        if ok and res:
            log.info("%s: el número %s queda como %s (pendiente de subir).", tabla,
                     numero, "ENTRADA" if sentido == "E" else "SALIDA")
        return bool(ok and res)

    def aplicar_config_remota(self, tipo, numero, sentido, config_fecha,
                              descripcion=None, activo=None, config_por=None,
                              id_terminal=None):
        """Adopta la configuración que mandó Bakelite, conservando SU fecha.

        Cambiar un sentido siempre mueve a los DOS dispositivos: si la API dice
        que la lectora 1 es ENTRADA, la 2 pasa a SALIDA por definición. Hacerlo
        de a uno viola el índice único a mitad de camino, así que va en una
        sola sentencia con CASE.

        Al otro dispositivo se le pone la misma fecha y origen: su sentido
        también cambió, y dejarlo con la fecha vieja haría que la comparación
        siguiente lo viera como una diferencia y lo mandara de vuelta.

        La guarda `ConfigFecha < fecha` sobre el dispositivo nombrado es la que
        impide pisar un cambio local más nuevo si los mensajes llegan
        desordenados.
        """
        if sentido not in ("E", "S"):
            log.error("Sentido inválido desde la API: %r", sentido)
            return False
        tabla = "Lectoras" if tipo == "lectora" else "Reles"
        col_activo = "Activa" if tipo == "lectora" else "Activo"
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal
        fecha = _texto_fecha(config_fecha)
        contrario = "S" if sentido == "E" else "E"

        def _fn(cur):
            self._asegurar_dispositivos(cur, idt)
            cur.execute(
                f"SELECT ConfigFecha FROM {tabla} WHERE IdTerminal = ? AND Numero = ?",
                (idt, numero))
            fila = cur.fetchone()
            if not fila or fila[0] >= fecha:
                return False
            # Mismo motivo que en _set_sentido: SQLite valida el índice único
            # fila por fila, así que el otro dispositivo se saca del índice un
            # instante con 'X' antes de ponerle el sentido contrario.
            cur.execute(f"UPDATE {tabla} SET Sentido = 'X' "
                        "WHERE IdTerminal = ? AND Numero != ?", (idt, numero))
            cur.execute(
                f"UPDATE {tabla} "
                "SET Sentido = ?, Descripcion = COALESCE(?, Descripcion), "
                f"    {col_activo} = COALESCE(?, {col_activo}), "
                "    ConfigFecha = ?, ConfigOrigen = 'API', ConfigPor = ?, Sincronizado = 1 "
                "WHERE IdTerminal = ? AND Numero = ?",
                (sentido, _corta(descripcion, 150), activo, fecha,
                 _corta(config_por, 100), idt, numero))
            afectadas = cur.rowcount
            cur.execute(
                f"UPDATE {tabla} SET Sentido = ?, ConfigFecha = ?, ConfigOrigen = 'API', "
                "    ConfigPor = ?, Sincronizado = 1 "
                "WHERE IdTerminal = ? AND Sentido = 'X'",
                (contrario, fecha, _corta(config_por, 100), idt))
            return afectadas > 0 and cur.rowcount > 0

        ok, res = self._ejecutar(_fn, "aplicar_config_remota")
        if ok and res:
            log.info("%s %s adoptado desde Bakelite: %s (%s). El otro pasa a %s.",
                     tipo, numero, sentido, fecha, contrario)
        return bool(ok and res)

    def dispositivos_pendientes(self, id_terminal=None):
        """Configuración cambiada aquí que todavía no llegó a Bakelite."""
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal
        return {"lectoras": [d for d in self.lectoras(idt) if not d["sincronizado"]],
                "reles": [d for d in self.reles(idt) if not d["sincronizado"]]}

    def marcar_dispositivos_sincronizados(self, tipo, numeros, id_terminal=None):
        """Lo subido deja de estar pendiente."""
        if not numeros:
            return True
        tabla = "Lectoras" if tipo == "lectora" else "Reles"
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal
        marcas = ",".join("?" for _ in numeros)

        def _fn(cur):
            cur.execute(
                f"UPDATE {tabla} SET Sincronizado = 1 "
                f"WHERE IdTerminal = ? AND Numero IN ({marcas})",
                (idt, *numeros))
            return cur.rowcount

        ok, res = self._ejecutar(_fn, "marcar_dispositivos_sincronizados")
        return bool(ok and res)

    # ---- Estado observado (solo lo escribe este equipo) ----
    def estado_lectora(self, numero, conectada=None, puerto=None, error=None,
                       ancla=None, id_terminal=None):
        """Guarda lo que se ve del hardware. Al reconectar se BORRA el último
        error: si solo se sobrescribiera cuando llega uno nuevo, una lectora
        que se desenchufa y se vuelve a enchufar quedaría conectada pero
        arrastrando el "Se desconectó del puerto" de antes."""
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal
        limpiar_error = bool(conectada) and error is None

        def _fn(cur):
            self._asegurar_dispositivos(cur, idt)
            cur.execute("SELECT UltimoPuerto, UltimoError, Ancla FROM Lectoras "
                        "WHERE IdTerminal = ? AND Numero = ?", (idt, numero))
            fila = cur.fetchone()
            if not fila:
                return False
            puerto_final = _corta(puerto, 100) if puerto is not None else fila[0]
            error_final = None if limpiar_error else (
                _corta(error, 500) if error is not None else fila[1])
            ancla_final = _corta(ancla, 200) if ancla is not None else fila[2]
            cur.execute(
                "UPDATE Lectoras "
                "SET Conectada = COALESCE(?, Conectada), UltimoPuerto = ?, "
                "    UltimoError = ?, Ancla = ?, EstadoFecha = ? "
                "WHERE IdTerminal = ? AND Numero = ?",
                (_bin(conectada), puerto_final, error_final, ancla_final,
                 _ahora_iso(), idt, numero))
            return cur.rowcount > 0

        ok, res = self._ejecutar(_fn, "estado_lectora")
        return bool(ok and res)

    def registrar_lectura(self, numero, id_terminal=None):
        """Momento de la última lectura de esa lectora."""
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal

        def _fn(cur):
            self._asegurar_dispositivos(cur, idt)
            ahora = _ahora_iso()
            cur.execute(
                "UPDATE Lectoras SET UltimaLectura = ?, Conectada = 1, EstadoFecha = ? "
                "WHERE IdTerminal = ? AND Numero = ?", (ahora, ahora, idt, numero))
            return cur.rowcount > 0

        ok, res = self._ejecutar(_fn, "registrar_lectura")
        return bool(ok and res)

    def registrar_disparo_rele(self, numero, error=None, id_terminal=None):
        """Momento del último accionamiento de ese relé."""
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal

        def _fn(cur):
            self._asegurar_dispositivos(cur, idt)
            ahora = _ahora_iso()
            cur.execute(
                "UPDATE Reles SET UltimoDisparo = ?, UltimoError = ?, EstadoFecha = ? "
                "WHERE IdTerminal = ? AND Numero = ?",
                (ahora, _corta(error, 500) if error is not None else None,
                 ahora, idt, numero))
            return cur.rowcount > 0

        ok, res = self._ejecutar(_fn, "registrar_disparo_rele")
        return bool(ok and res)

    def estado_arduino(self, conectado, puerto=None, id_terminal=None):
        """Hay un Arduino por terminal, así que su estado vive en Terminal."""
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal

        def _fn(cur):
            self._asegurar_terminal(cur, idt)
            cur.execute("SELECT ArduinoPuerto FROM Terminal WHERE IdTerminal = ?", (idt,))
            fila = cur.fetchone()
            puerto_final = _corta(puerto, 100) if puerto is not None else (fila[0] if fila else None)
            cur.execute(
                "UPDATE Terminal SET ArduinoConectado = ?, ArduinoPuerto = ?, "
                "    ArduinoEstadoFecha = ? WHERE IdTerminal = ?",
                (int(bool(conectado)), puerto_final, _ahora_iso(), idt))
            return cur.rowcount > 0

        ok, res = self._ejecutar(_fn, "estado_arduino")
        return bool(ok and res)

    def actualizar_puerto_lectora(self, numero, puerto):
        """Compatibilidad: la detección de puertos ya llamaba a esto."""
        return self.estado_lectora(numero, conectada=puerto is not None, puerto=puerto)
