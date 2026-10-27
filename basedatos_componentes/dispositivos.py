# -*- coding: utf-8 -*-
import json
import logging
from uuid import uuid4

import config
from .comun import _ahora_iso, _corta, _evento_largo, _texto_fecha

log = logging.getLogger("basedatos")

class DispositivosMixin:
    """Configuración y estado físico de lectoras, relés y Arduino."""

    def lectoras(self, id_terminal=None):
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal

        def _fn(cur):
            cur.execute(
                "SELECT Numero, Sentido, Descripcion, UltimoPuerto, Activa, "
                "       ConfigFecha, ConfigOrigen, ConfigPor, Sincronizado, "
                "       Conectada, UltimaLectura, UltimoError, Ancla "
                "FROM dbo.Lectoras WHERE IdTerminal = ? ORDER BY Numero", idt)
            return [{"numero": r[0], "sentido": r[1], "descripcion": r[2],
                     "puerto": r[3], "activa": bool(r[4]),
                     "config_fecha": r[5], "config_origen": r[6],
                     "config_por": r[7], "sincronizado": bool(r[8]),
                     "conectada": None if r[9] is None else bool(r[9]),
                     "ultima_lectura": r[10], "ultimo_error": r[11],
                     "ancla": r[12]}
                    for r in cur.fetchall()]

        ok, res = self._ejecutar(_fn, "lectoras")
        return res if ok else []

    def reles(self, id_terminal=None):
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal

        def _fn(cur):
            cur.execute(
                "SELECT Numero, Sentido, Comando, Descripcion, Activo, "
                "       ConfigFecha, ConfigOrigen, ConfigPor, Sincronizado, "
                "       UltimoDisparo, UltimoError "
                "FROM dbo.Reles WHERE IdTerminal = ? ORDER BY Numero", idt)
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
        return self._set_sentido("dbo.Lectoras", numero, sentido, usuario)

    def set_sentido_rele(self, numero, sentido, usuario=None):
        """Marca un relé como ENTRADA o SALIDA; el otro recibe el contrario."""
        return self._set_sentido("dbo.Reles", numero, sentido, usuario)

    def _set_sentido(self, tabla, numero, sentido, usuario=None, id_terminal=None):
        """El índice único garantiza una ENTRADA y una SALIDA por terminal, así
        que el cambio va en UNA sentencia con CASE: intercambia los dos a la vez
        y el motor valida el unique recién al terminarla. En dos UPDATE
        reventaría en el medio.

        Sella ConfigFecha y deja Sincronizado = 0: el cambio queda pendiente de
        subir a Bakelite, y esa fecha es la que decide quién gana si allá también
        lo cambiaron.
        """
        if sentido not in ("E", "S"):
            log.error("Sentido inválido: %r (debe ser E o S).", sentido)
            return False
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal
        contrario = "S" if sentido == "E" else "E"

        def _fn(cur):
            cur.execute(
                f"UPDATE {tabla} "
                "SET Sentido = CASE WHEN Numero = ? THEN ? ELSE ? END, "
                "    ConfigFecha = SYSDATETIMEOFFSET(), ConfigOrigen = 'LOCAL', "
                "    ConfigPor = ?, Sincronizado = 0, "
                "    FechaModificacion = SYSDATETIME(), ModificadoPor = ? "
                "WHERE IdTerminal = ?",
                numero, sentido, contrario, _corta(usuario, 100),
                _corta(usuario, 100), idt)
            return cur.rowcount > 0

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
        de a uno viola el índice único a mitad de camino, así que va en una sola
        sentencia con CASE y el motor valida al terminarla.

        Al otro dispositivo se le pone la misma fecha y origen: su sentido
        también cambió, y dejarlo con la fecha vieja haría que la comparación
        siguiente lo viera como una diferencia y lo mandara de vuelta.

        La guarda `ConfigFecha < @fecha` sobre el dispositivo nombrado es la que
        impide pisar un cambio local más nuevo si los mensajes llegan
        desordenados. Guardar la hora de aplicación en vez de la recibida haría
        que la copia local pareciera siempre más nueva y el cambio rebotaría.
        """
        if sentido not in ("E", "S"):
            log.error("Sentido inválido desde la API: %r", sentido)
            return False
        tabla = "dbo.Lectoras" if tipo == "lectora" else "dbo.Reles"
        col_activo = "Activa" if tipo == "lectora" else "Activo"
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal
        fecha = _texto_fecha(config_fecha)
        contrario = "S" if sentido == "E" else "E"

        def _fn(cur):
            cur.execute(
                f"UPDATE {tabla} "
                "SET Sentido = CASE WHEN Numero = ? THEN ? ELSE ? END, "
                "    Descripcion = CASE WHEN Numero = ? THEN COALESCE(?, Descripcion) "
                "                       ELSE Descripcion END, "
                f"    {col_activo} = CASE WHEN Numero = ? THEN COALESCE(?, {col_activo}) "
                f"                        ELSE {col_activo} END, "
                "    ConfigFecha = ?, ConfigOrigen = 'API', ConfigPor = ?, "
                "    Sincronizado = 1 "
                "WHERE IdTerminal = ? "
                f"  AND EXISTS (SELECT 1 FROM {tabla} d "
                "               WHERE d.IdTerminal = ? AND d.Numero = ? "
                "                 AND d.ConfigFecha < ?)",
                numero, sentido, contrario,
                numero, _corta(descripcion, 150),
                numero, activo,
                fecha, _corta(config_por, 100),
                idt, idt, numero, fecha)
            return cur.rowcount > 0

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
        """Lo subido deja de estar pendiente. Solo se limpia si la fecha no
        cambió mientras se subía: si el operador tocó el sentido en ese
        intervalo, el pendiente sigue vivo."""
        if not numeros:
            return True
        tabla = "dbo.Lectoras" if tipo == "lectora" else "dbo.Reles"
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal
        marcas = ",".join("?" for _ in numeros)

        def _fn(cur):
            cur.execute(
                f"UPDATE {tabla} SET Sincronizado = 1 "
                f"WHERE IdTerminal = ? AND Numero IN ({marcas})",
                idt, *numeros)
            return cur.rowcount

        ok, res = self._ejecutar(_fn, "marcar_dispositivos_sincronizados")
        return bool(ok and res)

    # ---- Estado observado (solo lo escribe este equipo) ----
    def estado_lectora(self, numero, conectada=None, puerto=None, error=None,
                       ancla=None, id_terminal=None):
        """Guarda lo que se ve del hardware. Lo escribe la detección de puertos
        cada vez que algo cambia, para que la pantalla y Bakelite muestren el
        estado real y no el último que alguien recuerde.

        Al reconectar se BORRA el último error. Antes solo se sobrescribía si
        llegaba uno nuevo, así que una lectora que se desenchufaba y se volvía a
        enchufar quedaba conectada pero arrastrando el "Se desconectó del
        puerto": en la web seguía viéndose como si algo anduviera mal.
        """
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal
        limpiar_error = bool(conectada) and error is None

        def _fn(cur):
            cur.execute(
                "UPDATE dbo.Lectoras "
                "SET Conectada = COALESCE(?, Conectada), "
                "    UltimoPuerto = CASE WHEN ? IS NULL THEN UltimoPuerto ELSE ? END, "
                "    UltimoError = CASE WHEN ? = 1 THEN NULL "
                "                       WHEN ? IS NULL THEN UltimoError ELSE ? END, "
                "    Ancla = CASE WHEN ? IS NULL THEN Ancla ELSE ? END, "
                "    EstadoFecha = SYSDATETIMEOFFSET() "
                "WHERE IdTerminal = ? AND Numero = ?",
                conectada, puerto, _corta(puerto, 100),
                limpiar_error, error, _corta(error, 500),
                ancla, _corta(ancla, 200), idt, numero)
            return cur.rowcount > 0

        ok, res = self._ejecutar(_fn, "estado_lectora")
        return bool(ok and res)

    def registrar_lectura(self, numero, id_terminal=None):
        """Momento de la última lectura de esa lectora."""
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal

        def _fn(cur):
            cur.execute(
                "UPDATE dbo.Lectoras "
                "SET UltimaLectura = SYSDATETIMEOFFSET(), Conectada = 1, "
                "    EstadoFecha = SYSDATETIMEOFFSET() "
                "WHERE IdTerminal = ? AND Numero = ?", idt, numero)
            return cur.rowcount > 0

        ok, res = self._ejecutar(_fn, "registrar_lectura")
        return bool(ok and res)

    def registrar_disparo_rele(self, numero, error=None, id_terminal=None):
        """Momento del último accionamiento de ese relé."""
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal

        def _fn(cur):
            cur.execute(
                "UPDATE dbo.Reles "
                "SET UltimoDisparo = SYSDATETIMEOFFSET(), "
                "    UltimoError = CASE WHEN ? IS NULL THEN NULL ELSE ? END, "
                "    EstadoFecha = SYSDATETIMEOFFSET() "
                "WHERE IdTerminal = ? AND Numero = ?",
                error, _corta(error, 500), idt, numero)
            return cur.rowcount > 0

        ok, res = self._ejecutar(_fn, "registrar_disparo_rele")
        return bool(ok and res)

    def estado_arduino(self, conectado, puerto=None, id_terminal=None):
        """Hay un Arduino por terminal, así que su estado vive en Terminales."""
        idt = config.ID_TERMINAL if id_terminal is None else id_terminal

        def _fn(cur):
            cur.execute(
                "UPDATE dbo.Terminales "
                "SET ArduinoConectado = ?, "
                "    ArduinoPuerto = CASE WHEN ? IS NULL THEN ArduinoPuerto ELSE ? END, "
                "    ArduinoEstadoFecha = SYSDATETIMEOFFSET() "
                "WHERE IdTerminal = ?",
                bool(conectado), puerto, _corta(puerto, 100), idt)
            return cur.rowcount > 0

        ok, res = self._ejecutar(_fn, "estado_arduino")
        return bool(ok and res)

    def actualizar_puerto_lectora(self, numero, puerto):
        """Compatibilidad: la detección de puertos ya llamaba a esto."""
        return self.estado_lectora(numero, conectada=puerto is not None,
                                   puerto=puerto)

