# -*- coding: utf-8 -*-
"""Panel de diagnóstico y confirmación de modo debugger."""

import interfaz as _base

globals().update({nombre: getattr(_base, nombre) for nombre in dir(_base)
                  if not nombre.startswith("__")})

class DebugMixin:
    """Depuración visual sin mezclarla con la ventana operativa."""

    def alternar_debugger(self):
        """Pide confirmación antes de partir la pantalla en dos."""
        if self.debug_activo:
            self.activar_debugger(False)
            return
        self._confirmar(
            "Entrar en modo debugger",
            "La pantalla se divide en dos: a la izquierda la aplicación, a la "
            "derecha el registro paso a paso de lo que se hace y lo que se "
            "recibe.\n\nEl registro se guarda en el equipo y se conserva al "
            "salir, así se puede revisar después.",
            "Entrar", lambda: self.activar_debugger(True))

    def _confirmar(self, titulo, texto, etiqueta_ok, al_aceptar):
        """Diálogo de confirmación propio: los de tkinter usan el tema del
        sistema y no pegan con el resto."""
        # Se cuelga de la ventana que esté arriba: si la confirmación nace del
        # diálogo de Ajustes y se hace transient de la raíz, queda por debajo.
        padre = self.root
        if self._dlg is not None and tk.Toplevel.winfo_exists(self._dlg):
            padre = self._dlg

        dlg = tk.Toplevel(padre, bg=BG)
        dlg.title(titulo)
        dlg.transient(padre)
        dlg.resizable(False, False)

        tk.Label(dlg, text=titulo.upper(), font=F(14, True), fg=TXT, bg=BG)\
            .pack(anchor="w", padx=26, pady=(22, 8))
        tk.Label(dlg, text=texto, font=F(10), fg=DIM, bg=BG, wraplength=460,
                 justify="left").pack(anchor="w", padx=26)

        botones = tk.Frame(dlg, bg=BG)
        botones.pack(fill="x", padx=26, pady=(20, 20))

        def aceptar():
            dlg.destroy()
            al_aceptar()

        widgets.RoundedButton(botones, etiqueta_ok, GREEN, "#0c1626", aceptar, BG,
                              F(10, True), hover="#54e08f", r=12, padx=20, pady=9,
                              ancho=self.ANCHO_BOTON).pack(side="right")
        widgets.RoundedButton(botones, "Cancelar", BG, TXT, dlg.destroy, BG,
                              F(10, True), hover=BG, r=12, padx=20, pady=9,
                              ancho=self.ANCHO_BOTON, borde=BORDE)\
            .pack(side="right", padx=(0, 10))
        # Recién ahora, con el contenido puesto y la ventana ya dibujada, se
        # puede centrar y tomar el foco. grab_set() sobre una ventana que aún no
        # es visible lanza "grab failed: window not viewable", y esa excepción
        # dejaba el diálogo a medio construir: por eso salía vacío.
        dlg.update_idletasks()
        x = padre.winfo_rootx() + (padre.winfo_width() - dlg.winfo_width()) // 2
        y = padre.winfo_rooty() + max((padre.winfo_height() - dlg.winfo_height()) // 3, 40)
        dlg.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        dlg.lift()
        dlg.attributes("-topmost", True)

        def tomar_foco():
            try:
                dlg.grab_set()
                dlg.focus_force()
            except tk.TclError as e:      # la ventana se cerró mientras tanto
                log.debug("No se pudo tomar el foco del diálogo: %s", e)

        dlg.after(60, tomar_foco)

    def activar_debugger(self, activo):
        if activo == self.debug_activo:
            return
        self.debug_activo = activo
        if activo:
            self._crear_panel_debug()
            depurador.suscribir(self._debug_entrante)
            depurador.activo = True
            depurador.accion("Modo debugger activado", origen="interfaz")
        else:
            depurador.accion("Modo debugger desactivado", origen="interfaz")
            depurador.activo = False
            depurador.desuscribir(self._debug_entrante)
            if self._panel_debug is not None:
                self._panel_debug.destroy()
                self._panel_debug = None
                self._texto_debug = None
            self.root.grid_columnconfigure(1, weight=0, minsize=0)

    def _crear_panel_debug(self):
        # La mitad derecha: mismo peso que la aplicación, con un mínimo para
        # que el registro siga siendo legible en pantallas chicas.
        self.root.grid_columnconfigure(1, weight=1, minsize=420)
        panel = tk.Frame(self.root, bg=BG, padx=18, pady=14)
        panel.grid(row=0, column=1, sticky="nsew")
        panel.grid_rowconfigure(2, weight=1)
        panel.grid_columnconfigure(0, weight=1)
        self._panel_debug = panel

        cab = tk.Frame(panel, bg=BG)
        cab.grid(row=0, column=0, sticky="ew")
        tk.Label(cab, text="🐞  MODO DEBUGGER", font=F(13, True), fg=TXT, bg=BG)\
            .pack(side="left")
        widgets.RoundedButton(cab, "Salir", BG, TXT,
                              lambda: self.activar_debugger(False), BG, F(10, True),
                              hover=BG, r=10, padx=14, pady=6, ancho=96,
                              borde=BORDE)\
            .pack(side="right")
        widgets.RoundedButton(cab, "Limpiar", BG, DIM, self._debug_limpiar, BG,
                              F(10, True), hover=BG, r=10, padx=14, pady=6,
                              ancho=96, borde=BORDE).pack(side="right", padx=(0, 8))

        tk.Label(panel, text=f"→ lo que se hace    ← lo que se recibe    ·  detalle\n"
                             f"{depurador.ruta}",
                 font=F(8), fg=DIM2, bg=BG, justify="left")\
            .grid(row=1, column=0, sticky="w", pady=(4, 8))

        caja = tk.Frame(panel, bg=BG)
        caja.grid(row=2, column=0, sticky="nsew")
        barra = tk.Scrollbar(caja, orient="vertical", bg=CARD_BD, troughcolor=BG,
                             activebackground=DIM2, highlightthickness=0,
                             borderwidth=0, relief="flat", width=10)
        barra.pack(side="right", fill="y")
        texto = tk.Text(caja, bg=CARD, fg=TXT, insertbackground=TXT, relief="flat",
                        font=("DejaVu Sans Mono", 9), wrap="word", padx=12, pady=10,
                        yscrollcommand=barra.set, highlightthickness=0)
        texto.pack(side="left", fill="both", expand=True)
        barra.config(command=texto.yview)
        texto.tag_configure("accion", foreground=BLUE)
        texto.tag_configure("respuesta", foreground=GREEN)
        texto.tag_configure("error", foreground=RED)
        texto.tag_configure("info", foreground=DIM2)
        self._texto_debug = texto

        # Lo que ya estaba registrado: el modo debugger sirve justo para
        # revisar lo que pasó antes de abrirlo.
        for linea in depurador.historial():
            self._debug_escribir(linea)
        texto.config(state="disabled")

    def _debug_entrante(self, linea):
        """Llega desde cualquier hilo: se pasa por la cola de Tk."""
        self.cola.put(lambda: self._debug_agregar(linea))

    def _debug_agregar(self, linea):
        if self._texto_debug is None:
            return
        self._texto_debug.config(state="normal")
        self._debug_escribir(linea)
        self._texto_debug.config(state="disabled")
        self._texto_debug.see("end")

    def _debug_escribir(self, linea):
        if "ERROR" in linea:
            tag = "error"
        elif "  →  " in linea:
            tag = "accion"
        elif "  ←  " in linea:
            tag = "respuesta"
        else:
            tag = "info"
        self._texto_debug.insert("end", linea + "\n", tag)

    def _debug_limpiar(self):
        depurador.limpiar()
        if self._texto_debug is not None:
            self._texto_debug.config(state="normal")
            self._texto_debug.delete("1.0", "end")
            self._texto_debug.config(state="disabled")
        depurador.accion("Registro limpiado", origen="interfaz")

