# -*- coding: utf-8 -*-
"""Estado visual y comunicación segura con el hilo de Tk."""

import interfaz as _base

globals().update({nombre: getattr(_base, nombre) for nombre in dir(_base)
                  if not nombre.startswith("__")})

class EstadoOperacionMixin:
    """Resultados, historial y actualizaciones encoladas desde otros hilos."""

    def mostrar_esperando(self):
        self.cola.put(self._ui_esperando)

    def mostrar_consultando(self, sentido):
        self.cola.put(lambda: self._ui_consultando(sentido))

    def mostrar_resultado(self, resultado):
        self.cola.put(lambda: self._ui_resultado(resultado))

    def set_luz(self, color):
        self.cola.put(lambda: self._ui_luz(color))

    def cargar_historial(self, marcas):
        """Precarga el historial con las últimas marcas de la BD local, para que
        al abrir la app no aparezca vacío. `marcas` viene de BDLocal.ultimas_marcas()
        e incluye las que todavía no se han subido a Bakelite."""
        self.cola.put(lambda: self._ui_cargar_historial(marcas))

    def set_estado_hw(self, estado):
        """Refresca el estado del hardware (lectoras y Arduino) desde el hilo
        de vigilancia de puertos."""
        self.cola.put(lambda: self._aplicar_estado(estado))

    def set_en_linea(self, en_linea, ultima=None, servicio="bakelite"):
        """Estado de un servicio externo: 'bakelite' o 'externa'."""
        self.cola.put(lambda: self._ui_en_linea(en_linea, ultima, servicio))

    def mostrar_critico(self, texto):
        self.cola.put(lambda: self._mostrar_critico(texto))

    def limpiar_critico(self):
        self.cola.put(self._limpiar_critico)

    def recargar_dispositivos(self):
        """Bakelite cambió la configuración: se relee y se repinta. Llega desde
        el hilo de sincronización, así que pasa por la cola de Tk."""
        self.cola.put(self._ui_recargar_dispositivos)

    def _ui_recargar_dispositivos(self):
        aj = getattr(self.controlador, "ajustes", None) if self.controlador else None
        if aj is not None:
            aj.cargar_de_bd()
        # El aviso de arriba nombra las lectoras por su sentido: si cambió, ahí
        # también tiene que cambiar.
        self._aplicar_estado(self.estado_hw)
        refrescar = getattr(self, "_refrescar_lectoras", None)
        if refrescar and self._dlg is not None and tk.Toplevel.winfo_exists(self._dlg):
            try:
                refrescar()
            except Exception as e:  # noqa: BLE001
                log.debug("No se pudo refrescar el estado de dispositivos: %s", e)

    def set_nombre_terminal(self, nombre):
        """El nombre cambió en Bakelite y el sincronizador lo adoptó. Llega
        desde el hilo del sincronizador, así que pasa por la cola."""
        self.cola.put(lambda: self._ui_nombre_terminal(nombre))

    def _drenar_cola(self):
        try:
            while True:
                fn = self.cola.get_nowait()
                try:
                    fn()
                except Exception as e:  # noqa: BLE001
                    log.error("Error actualizando UI: %s", e)
        except queue.Empty:
            pass
        self.root.after(50, self._drenar_cola)

    def _set_banner(self, color, texto):
        self._pintar_punto(self.dot, _nombre_color(color))
        self.lbl_estado.config(fg=color, text=texto)

    def _ui_esperando(self):
        self._set_banner(GREEN, "ACERQUE SU CÉDULA DE IDENTIDAD AL LECTOR")
        if self._vista_torniquete is not None:
            self.t_banner.config(text="ACERQUE SU CÉDULA", fg=GREEN)
            self._t_limpiar()

    def _ui_consultando(self, sentido):
        self._set_banner(BLUE, "VALIDANDO ACCESO…")
        if self._vista_torniquete is not None:
            self.t_banner.config(text="VALIDANDO…", fg=BLUE)
            self._t_limpiar()

    def _ui_resultado(self, r):
        codigo = r.codigo
        if codigo == 1:
            self._set_banner(GREEN, "ACCESO AUTORIZADO")
            self._veredicto("ok", "AUTORIZADO", GREEN)
            self._actualizar_registro(r)
        elif codigo == 4:
            self._set_banner(YELLOW, "SIN CONEXIÓN A RED")
            self._veredicto("warn", "SIN RED", YELLOW)
        elif codigo == 5:
            # La consulta se pasó del tope: no se sabe si la persona puede
            # pasar, así que se le pide que lo intente de nuevo.
            self._set_banner(YELLOW, "SIN RESPUESTA — VUELVA A INTENTAR")
            self._veredicto("warn", "REINTENTE", YELLOW)
        elif codigo in (2, 3):
            self._set_banner(RED, "ERROR DE LECTURA — REINTENTE")
            self._veredicto("no", "ERROR", RED)
        else:
            self._set_banner(RED, "ACCESO NO HABILITADO")
            self._veredicto("no", "DENEGADO", RED)
            self._actualizar_registro(r)
        if codigo in (0, 1):
            self._push_historial(r)

        if self._vista_torniquete is not None:
            self.t_banner.config(text=self.lbl_estado.cget("text"),
                                 fg=self.lbl_estado.cget("fg"))
            self._t_resultado(r)

    def _ui_luz(self, color):
        c, etq, _sig = LUCES.get(color, LUCES["off"])
        if self._luz_ok:
            self.luz_big.config(image=self._luz_img[color])
        self.luz_cap.config(text=etq, fg=c)
        if self._vista_torniquete is not None:
            self._t_pintar_luz(color)

    def _ui_en_linea(self, en_linea, ultima, servicio="bakelite"):
        if servicio == "externa":
            luz, lbl, nombre = self.luz_externa, self.lbl_externa, "API externa"
        else:
            luz, lbl, nombre = self.luz_online, self.lbl_conexion, "Bakelite"

        if en_linea is None:
            # Todavía no se sabe: no se declara caída hasta comprobarlo.
            self._pintar_punto(luz, "amarillo")
            txt = f"{nombre}: verificando…"
            if ultima:
                txt += f"  ·  última conexión {self._hace_cuanto(ultima)}"
            lbl.config(text=txt, fg=DIM)
            self._estado_servicios[servicio] = (None, txt)
            self._t_servicio(servicio, None, txt)
            return

        self._pintar_punto(luz, "verde" if en_linea else "rojo")
        if en_linea:
            txt = f"{nombre}: en línea"
        else:
            # Caído: lo importante es hace cuánto que no responde.
            txt = f"{nombre}: SIN CONEXIÓN"
            if ultima:
                txt += f"  ·  última conexión {self._hace_cuanto(ultima)}"
        if en_linea and ultima:
            try:
                txt += f"  ·  {self._hora_hm(ultima)}"
            except Exception as e:  # noqa: BLE001
                log.debug("No se pudo formatear la última conexión: %s", e)
        lbl.config(text=txt, fg=DIM if en_linea else RED)
        self._estado_servicios[servicio] = (en_linea, txt)
        self._t_servicio(servicio, en_linea, txt)

    @staticmethod
    def _hace_cuanto(dt):
        """'hace 3 min' / 'hace 2 h 15 min', para el indicador de sin conexión."""
        try:
            ahora = datetime.datetime.now(dt.tzinfo) if dt.tzinfo else datetime.datetime.now()
            seg = int((ahora - dt).total_seconds())
        except Exception:  # noqa: BLE001
            return ""
        if seg < 60:
            return "hace menos de 1 min"
        minutos, horas = seg // 60, seg // 3600
        if horas < 1:
            return f"hace {minutos} min"
        if horas < 24:
            return f"hace {horas} h {minutos % 60} min"
        return f"hace {horas // 24} d {horas % 24} h"

    def _veredicto(self, tipo, texto, color):
        if self._badge_ok:
            self.badge.config(image=self._badge_img[tipo])
        else:
            simbolo = {"ok": "✔", "no": "✖", "warn": "!"}[tipo]
            self._circ_lbl.config(fg=color)
            self._sym_lbl.config(text=simbolo, bg=color)
        self.lbl_veredicto.config(fg=color, text=texto)

    def _actualizar_registro(self, r):
        self.lbl_nombre.config(text=recorta(r.nombre, NOMBRE_MAX))
        self.lbl_cedula.config(
            text=f"Cédula: {_rut_visual(r.rut_display or r.rut_norm, '—')}"
        )
        self.lbl_hora_reg.config(text=self._hora_hm(datetime.datetime.now()))
        if r.sentido == "E":
            widgets.set_pill(self.lbl_tag, "ENTRADA", GREEN, "#0c1626", CARD, F(10, True),
                             padx=14, pady=4)
        else:
            widgets.set_pill(self.lbl_tag, "SALIDA", BLUE, "#0c1626", CARD, F(10, True),
                             padx=14, pady=4)

    def _ui_cargar_historial(self, marcas):
        self.historial = []
        for m in marcas[:HIST_MEMORIA]:
            self.historial.append({
                "hora": self._hora_hm(m.get("fecha")) if m.get("fecha") else "—",
                "nombre": m.get("nombre") or "Desconocido",
                "rut": _rut_visual(m.get("rut"), "—"),
                "sentido": "E" if (m.get("evento") == "ENTRADA") else "S",
                "autorizado": bool(m.get("habilitado")),
                "motivo": m.get("motivo") or "",
                "pendiente": m.get("estado_envio") != "ENVIADA",
            })
        if self.historial:
            self._ui_ultimo_registro(self.historial[0])
        self._render_historial()

    def _ui_ultimo_registro(self, ev):
        """Llena el panel grande con una marca traída de la BD, para que al
        abrir la app no quede vacío mientras la tabla ya muestra historial."""
        self.lbl_nombre.config(text=recorta(ev["nombre"], NOMBRE_MAX))
        self.lbl_cedula.config(text=f"Cédula: {_rut_visual(ev.get('rut'), '—')}")
        self.lbl_hora_reg.config(text=ev["hora"])
        if ev["sentido"] == "E":
            widgets.set_pill(self.lbl_tag, "ENTRADA", GREEN, "#0c1626", CARD, F(10, True),
                             padx=14, pady=4)
        else:
            widgets.set_pill(self.lbl_tag, "SALIDA", BLUE, "#0c1626", CARD, F(10, True),
                             padx=14, pady=4)
        if ev["autorizado"]:
            self._veredicto("ok", "AUTORIZADO", GREEN)
        else:
            self._veredicto("no", "DENEGADO", RED)

    def _push_historial(self, r):
        self.historial.insert(0, {
            "hora": self._hora_hm(datetime.datetime.now()),
            "nombre": r.nombre or "Desconocido",
            "rut": _rut_visual(r.rut_display or r.rut_norm, "—"),
            "sentido": r.sentido,
            "autorizado": bool(r.autorizado),
            "motivo": r.motivo or "",
            "pendiente": True,       # recién creada: aún no se sube a Bakelite
        })
        del self.historial[HIST_MEMORIA:]
        self._render_historial()

    def _crear_filas_historial(self):
        """Crea de una vez las filas del historial. Antes se destruían y se
        volvían a crear en cada acceso, y eso hacía parpadear la pantalla
        completa. Ahora los widgets viven siempre y solo se les cambia el texto."""
        self._hist_filas = []
        self._hist_vacio = tk.Label(self.hist_cont, text="Sin registros aún.",
                                    font=F(11), fg=DIM2, bg=CARD, anchor="w")

        for _ in range(HIST_FILAS):
            fila = tk.Frame(self.hist_cont, bg=CARD)
            hora = tk.Label(fila, text="", font=F(11), fg=DIM, bg=CARD,
                            width=10, anchor="w")
            hora.pack(side="left", padx=(14, 8))
            nombre = tk.Label(fila, text="", font=F(12, True), fg=TXT, bg=CARD,
                              anchor="w", width=NOMBRE_HIST_MAX)
            nombre.pack(side="left")

            # Se empaquetan primero los de la derecha para que fijen su borde.
            veredicto = tk.Label(fila, text="", font=F(10, True), fg=GREEN, bg=CARD,
                                 width=13, anchor="e")
            veredicto.pack(side="right", padx=(0, 14))
            tag = tk.Label(fila, text="", font=F(9, True), fg=GREEN, bg=CARD, width=8)
            tag.pack(side="right", padx=(0, 12))
            rut = tk.Label(fila, text="", font=F(11), fg=DIM, bg=CARD,
                           width=13, anchor="e")
            rut.pack(side="right", padx=(0, 10))

            # El motivo ocupa el espacio que sobra entre el nombre y el RUT.
            motivo = tk.Label(fila, text="", font=F(10), fg=DIM2, bg=CARD, anchor="w")
            motivo.pack(side="left", fill="x", expand=True, padx=(6, 10))

            self._hist_filas.append({"fila": fila, "hora": hora, "nombre": nombre,
                                     "motivo": motivo, "rut": rut, "tag": tag,
                                     "veredicto": veredicto})

    def _render_historial(self):
        """Actualiza las filas ya existentes: sin destruir ni crear widgets, la
        pantalla no parpadea al llegar una lectura.

        Se omite el primer registro: ese ya se ve completo, y en grande, en el
        panel "ÚLTIMO REGISTRO" de arriba."""
        if not getattr(self, "_hist_filas", None):
            return

        anteriores = self.historial[1:]
        if not anteriores:
            for f in self._hist_filas:
                f["fila"].pack_forget()
            if not self._hist_vacio.winfo_manager():
                self._hist_vacio.pack(anchor="w", padx=6, pady=6)
            return

        if self._hist_vacio.winfo_manager():
            self._hist_vacio.pack_forget()

        for idx, f in enumerate(self._hist_filas):
            if idx >= len(anteriores):
                f["fila"].pack_forget()
                continue

            ev = anteriores[idx]
            col = GREEN if ev["autorizado"] else RED
            # El punto marca que la marca aún no se ha subido a Bakelite.
            f["hora"].config(text=ev["hora"] + (" ·" if ev.get("pendiente") else ""))
            f["nombre"].config(text=recorta(ev["nombre"], NOMBRE_HIST_MAX))
            # Solo se muestra cuando hay algo que explicar: en una autorizada
            # la columna queda vacía, no con un guion de relleno.
            motivo = (ev.get("motivo") or "").strip()
            f["motivo"].config(text=recorta(motivo, MOTIVO_HIST_MAX) if motivo else "",
                               fg=RED if motivo else DIM2)
            f["rut"].config(text=ev["rut"])
            f["tag"].config(text="ENTRADA" if ev["sentido"] == "E" else "SALIDA",
                            fg=GREEN if ev["sentido"] == "E" else BLUE)
            f["veredicto"].config(text="✓ AUTORIZADO" if ev["autorizado"] else "✕ DENEGADO",
                                  fg=col)
            if not f["fila"].winfo_manager():
                f["fila"].pack(fill="x", pady=3, ipady=7)

