# -*- coding: utf-8 -*-
"""Componente interno de la interfaz Tk."""

import interfaz as _base

globals().update({
    nombre: getattr(_base, nombre)
    for nombre in dir(_base)
    if not nombre.startswith("__")
})

class ConstruccionMixin:
    """Estructura de la ventana principal y sus paneles permanentes."""

    def _construir(self):
        # Toda la aplicación vive dentro de un contenedor con scroll, no
        # directamente sobre la ventana. En modo debugger la pantalla se parte
        # en dos y esta mitad queda angosta: sin scroll, el pie y los botones
        # quedarían fuera de alcance.
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(0, weight=1)
        self._sistema, self._sistema_barra = self._contenedor_sistema()

        r = self._sistema
        r.grid_rowconfigure(2, weight=1)
        r.grid_columnconfigure(0, weight=1)
        self._barra_superior()
        self._banner()
        self._centro()
        self._pie()
        self._powered_by()
        # Lo que desaparece en modo torniquete. La barra superior no está aquí
        # a propósito: contiene el switch para volver a modo PC.
        self._zonas_pc = [w for w in self._sistema.grid_slaves()
                          if int(w.grid_info().get("row", 0)) > 0]
        if self.sim:
            tk.Label(
                r,
                text="SIM  ·  1–6 entrada   ·   Ctrl+1–6 salida   ·   0 no registrado"
                     "   ·   .  error de lectura   ·   -  sin conexión",
                font=F(9), fg=DIM2, bg=BG,
            ).grid(row=4, column=0, pady=(0, 8))

    def _enganchar_rueda(self, zona, lienzo, hay_scroll=None):
        """Hace que la rueda desplace `lienzo` mientras el puntero esté sobre
        `zona`.

        Antes esto se hacía con bind_all, que instala el manejador para toda la
        aplicación: cada diálogo nuevo pisaba al anterior y, al cerrarse, dejaba
        uno apuntando a widgets ya destruidos. Mover la rueda después reventaba
        con «bad window path name». Enganchando al entrar y soltando al salir
        —y al destruirse— no quedan manejadores huérfanos.
        """
        def rueda(e):
            if hay_scroll is not None and not hay_scroll():
                return
            try:
                lienzo.yview_scroll(-1 if getattr(e, "num", 0) == 4 or
                                    getattr(e, "delta", 0) > 0 else 1, "units")
            except tk.TclError:
                self._soltar_rueda()

        def entrar(_e=None):
            for sec in self.RUEDA:
                self.root.bind_all(sec, rueda)

        zona.bind("<Enter>", entrar)
        zona.bind("<Leave>", lambda e: self._soltar_rueda())
        zona.bind("<Destroy>", lambda e: self._soltar_rueda())

    def _soltar_rueda(self):
        for sec in self.RUEDA:
            try:
                self.root.unbind_all(sec)
            except tk.TclError:
                pass

    def _contenedor_sistema(self):
        """La app dentro de un canvas: permite desplazarla cuando no cabe.

        El alto del contenido se fuerza al del canvas mientras quepa, para que
        la pantalla siga estirándose como siempre; solo cuando de verdad no
        entra se le deja su alto natural y aparece la barra.
        """
        marco = tk.Frame(self.root, bg=BG)
        marco.grid(row=0, column=0, sticky="nsew")

        lienzo = tk.Canvas(marco, bg=BG, highlightthickness=0)
        barra = tk.Scrollbar(marco, orient="vertical", command=lienzo.yview,
                             bg=CARD_BD, troughcolor=BG, activebackground=DIM2,
                             highlightthickness=0, borderwidth=0, relief="flat",
                             width=10)
        interno = tk.Frame(lienzo, bg=BG)
        ventana = lienzo.create_window((0, 0), window=interno, anchor="nw")
        lienzo.configure(yscrollcommand=barra.set)
        lienzo.pack(side="left", fill="both", expand=True)

        def ajustar(evento=None):
            # Las medidas se toman del propio evento cuando viene del canvas:
            # winfo_height() durante un <Configure> todavía informa el tamaño
            # anterior, y con eso el contenido se quedaba con el alto viejo
            # (una ventana achicada a 600 seguía midiendo 848 por dentro).
            if evento is not None and evento.widget is lienzo:
                ancho, alto = evento.width, evento.height
            else:
                ancho, alto = lienzo.winfo_width(), lienzo.winfo_height()
            necesario = interno.winfo_reqheight()
            lienzo.itemconfig(ventana, width=ancho, height=max(necesario, alto))
            lienzo.configure(scrollregion=lienzo.bbox("all"))
            sobra = necesario > alto
            if sobra and not barra.winfo_ismapped():
                barra.pack(side="right", fill="y", before=lienzo)
            elif not sobra and barra.winfo_ismapped():
                barra.pack_forget()

        interno.bind("<Configure>", ajustar)
        lienzo.bind("<Configure>", ajustar)
        # El alto del contenido queda fijado por itemconfig, así que si el
        # ajuste no vuelve a correr se conserva el de antes. Cambiar de modo no
        # dispara <Configure> del canvas, y la vista quedaba con el alto viejo.
        self._ajustar_sistema = lambda: ajustar(None)

        self._enganchar_rueda(marco, lienzo, hay_scroll=barra.winfo_ismapped)
        return interno, barra

    def _barra_superior(self):
        top = tk.Frame(self._sistema, bg=BG)
        top.grid(row=0, column=0, sticky="ew", padx=44, pady=(24, 0))
        top.grid_columnconfigure(0, weight=1)

        left = tk.Frame(top, bg=BG)
        left.grid(row=0, column=0, sticky="w")

        if self._logo_bakelite is not None:
            # El logo ya trae la palabra BAKELITE: no se repite como texto.
            tk.Label(left, image=self._logo_bakelite, bg=BG, bd=0)\
                .pack(side="left", padx=(0, 16))
        else:
            self._logo(left)              # respaldo dibujado, si no hay Pillow

        textos = tk.Frame(left, bg=BG)
        textos.pack(side="left")
        linea1 = tk.Frame(textos, bg=BG)
        linea1.pack(anchor="w")
        if self._logo_bakelite is None:
            tk.Label(linea1, text=config.MARCA, font=F(20, True), fg=TXT, bg=BG)\
                .pack(side="left")
            tk.Label(linea1, text="   |   ", font=F(16), fg=DIM2, bg=BG).pack(side="left")
        tk.Label(linea1, text=config.APP_TITULO, font=F(13), fg=DIM, bg=BG).pack(side="left")
        tk.Label(textos, text=config.SUBTITULO, font=F(11), fg=YELLOW, bg=BG)\
            .pack(anchor="w", pady=(2, 0))

        # Reloj: hora grande (24 h) y fecha debajo.
        right = tk.Frame(top, bg=BG)
        right.grid(row=0, column=1, sticky="e")
        self.lbl_hora = tk.Label(right, text="", font=F(38, True), fg=TXT, bg=BG)
        self.lbl_hora.pack(anchor="e")
        self.lbl_fecha = tk.Label(right, text="", font=F(12), fg=DIM, bg=BG)
        self.lbl_fecha.pack(anchor="e")

        # Selector de modo. En el torniquete la pantalla es chica y quien pasa
        # solo necesita ver si puede pasar; toda la operación (historial,
        # aforo, ajustes) es del modo PC.
        sel = tk.Frame(right, bg=BG)
        sel.pack(anchor="e", pady=(8, 0))
        self._btn_modo = {}
        for modo, texto in (("torniquete", "TORNIQUETE"), ("pc", "PC")):
            b = widgets.RoundedButton(sel, texto, BG, DIM2, None, BG, F(9, True),
                                      r=9, padx=12, pady=5, ancho=104, borde=BORDE)
            b._command = lambda m=modo: self.set_modo(m)
            b.pack(side="left", padx=(6, 0))
            self._btn_modo[modo] = b
        self._pintar_modo()

    def _logo(self, parent):
        import math
        c = tk.Canvas(parent, width=40, height=40, bg=BG, highlightthickness=0)
        cx, cy, rad = 20, 20, 15
        pts = []
        for i in range(6):
            a = math.radians(60 * i - 90)
            pts += [cx + rad * math.cos(a), cy + rad * math.sin(a)]
        c.create_polygon(pts, outline=TXT, fill="", width=2)
        c.create_line(cx, cy + 7, cx, cy + 1, fill=TXT, width=2)
        c.create_line(cx, cy + 1, cx - 6, cy - 6, fill=TXT, width=2)
        c.create_line(cx, cy + 1, cx + 6, cy - 6, fill=TXT, width=2)
        c.pack(side="left", padx=(0, 12))

    def _banner(self):
        cont = tk.Frame(self._sistema, bg=BG)
        cont.grid(row=1, column=0, pady=(20, 0))

        self._banner_cont = cont
        self.lbl_critico = tk.Label(cont, text="", font=F(11, True), fg="#0c1626", bg=RED)
        # se muestra solo si hay error crítico (ver _mostrar_critico)

        # Contenedor GRANDE y fijo (transparente): así el mensaje no reacomoda ni
        # se corta al cambiar de largo. El grupo dot+texto se centra dentro.
        b = tk.Frame(cont, bg=BG, width=1100, height=46)
        self._banner_b = b
        b.pack()
        b.pack_propagate(False)
        grupo = tk.Frame(b, bg=BG)
        grupo.place(relx=0.5, rely=0.5, anchor="center")
        self.dot = self._crear_punto(grupo, "verde", tam=16)
        self.dot.pack(side="left", padx=(0, 12))
        self.lbl_estado = tk.Label(grupo, text="", font=F(19, True), fg=GREEN, bg=BG)
        self.lbl_estado.pack(side="left")

        self.lbl_aviso = tk.Label(cont, text="", font=F(11, True), fg=YELLOW, bg=BG,
                                  cursor="hand2")
        self.lbl_aviso.pack(pady=(8, 0))
        self.lbl_aviso.bind("<Button-1>", lambda e: self._abrir_estado())

    def _centro(self):
        centro = tk.Frame(self._sistema, bg=BG)
        centro.grid(row=2, column=0)
        self._panel_luz(centro)                       # columna izquierda
        der = tk.Frame(centro, bg=BG)
        der.grid(row=0, column=1, sticky="n")
        self._tarjeta(der)
        self._panel_historial(der)

    def _panel_luz(self, parent):
        # La columna llena el alto: luz principal ARRIBA, leyenda ABAJO (al costado).
        col = tk.Frame(parent, bg=BG)
        col.grid(row=0, column=0, sticky="ns", padx=(0, 30))

        arriba = tk.Frame(col, bg=BG)
        arriba.pack(side="top", anchor="n", pady=(6, 0))
        self.luz_big = tk.Label(arriba, image="", bg=BG, bd=0)
        self.luz_big.pack()
        self.luz_cap = tk.Label(arriba, text="EN ESPERA", font=F(13, True), fg=DIM, bg=BG)
        self.luz_cap.pack(pady=(8, 0))

        leg = widgets.RoundedPanel(col, 250, 176, 16, CARD, BG)
        leg.pack(side="bottom", anchor="s", pady=(0, 4))
        tk.Label(leg.inner, text="SEMÁFORO", font=F(9, True), fg=DIM2, bg=CARD).pack(anchor="w")
        for color in ("azul", "verde", "rojo", "amarillo"):
            c, _et, sig = LUCES[color]
            fila = tk.Frame(leg.inner, bg=CARD)
            fila.pack(anchor="w", pady=3, fill="x")
            tk.Label(fila, text="●", font=F(13), fg=c, bg=CARD).pack(side="left", padx=(0, 8))
            tk.Label(fila, text=sig, font=F(10), fg=TXT, bg=CARD).pack(side="left")

    def _tarjeta(self, parent):
        card = widgets.RoundedPanel(parent, CARD_W, CARD_H, 20, CARD, BG)
        card.pack(pady=(6, 10))
        inner = card.inner
        inner.grid_rowconfigure(0, weight=1)
        inner.grid_columnconfigure(0, weight=1)

        info = tk.Frame(inner, bg=CARD)
        info.grid(row=0, column=0, sticky="w", padx=(16, 10))
        tk.Label(info, text="ÚLTIMO REGISTRO", font=F(10, True), fg=DIM2, bg=CARD).pack(anchor="w")
        # Nombre en contenedor de ancho FIJO: no se reacomoda ni parpadea con
        # nombres largos (el texto ya viene recortado a NOMBRE_MAX).
        nombre_wrap = tk.Frame(info, bg=CARD, width=560, height=42)
        nombre_wrap.pack(anchor="w", pady=(2, 4))
        nombre_wrap.pack_propagate(False)
        self.lbl_nombre = tk.Label(nombre_wrap, text="—", font=F(28, True), fg=TXT, bg=CARD,
                                   anchor="w")
        self.lbl_nombre.pack(side="left", fill="both", expand=True)
        self.lbl_cedula = tk.Label(info, text="Cédula: —", font=F(14), fg=DIM, bg=CARD)
        self.lbl_cedula.pack(anchor="w")

        fila = tk.Frame(info, bg=CARD)
        fila.pack(anchor="w", pady=(12, 0))
        self.lbl_hora_reg = tk.Label(fila, text="—", font=F(12), fg=DIM, bg=CARD)
        self.lbl_hora_reg.pack(side="left", padx=(0, 14))
        self.lbl_tag = widgets.make_pill(fila, "ENTRADA", GREEN, "#0c1626", CARD,
                                         F(10, True), padx=14, pady=4)
        self.lbl_tag.pack(side="left")

        estado = tk.Frame(inner, bg=CARD)
        estado.grid(row=0, column=1, sticky="e", padx=(10, 16))
        if self._badge_ok:
            self.badge = tk.Label(estado, image="", bg=CARD, bd=0)
            self.badge.pack()
        else:
            self.badge = tk.Frame(estado, width=70, height=70, bg=CARD)
            self.badge.pack()
            self.badge.pack_propagate(False)
            self._circ_lbl = tk.Label(self.badge, text="●", font=(_base.FAM, 62), fg=CARD, bg=CARD)
            self._circ_lbl.place(relx=0.5, rely=0.5, anchor="center")
            self._sym_lbl = tk.Label(self.badge, text="", font=(_base.FAM, 20, "bold"),
                                     fg="#0c1626", bg=CARD)
            self._sym_lbl.place(relx=0.5, rely=0.46, anchor="center")
        self.lbl_veredicto = tk.Label(estado, text="", font=F(11, True), fg=GREEN, bg=CARD)
        self.lbl_veredicto.pack(pady=(8, 0))

    def _panel_historial(self, parent):
        panel = widgets.RoundedPanel(parent, HIST_W, HIST_H, 20, CARD, BG)
        panel.pack(pady=(2, 6))
        tk.Label(panel.inner, text="ÚLTIMOS REGISTROS", font=F(10, True), fg=DIM2, bg=CARD)\
            .pack(anchor="w", pady=(0, 6))
        self.hist_cont = tk.Frame(panel.inner, bg=CARD)
        self.hist_cont.pack(fill="both", expand=True)
        self._crear_filas_historial()

    def _powered_by(self):
        """Firma centrada al pie de la ventana, en su propia fila."""
        barra = tk.Frame(self._sistema, bg=BG)
        barra.grid(row=5, column=0, pady=(2, 14))
        tk.Label(barra, text="Powered by", font=F(9), fg=DIM2, bg=BG)\
            .pack(side="left", padx=(0, 9))
        if self._logo_sopytec is not None:
            tk.Label(barra, image=self._logo_sopytec, bg=BG, bd=0).pack(side="left")
        else:
            tk.Label(barra, text="sopytec", font=F(12, True), fg=TXT, bg=BG).pack(side="left")

    def _pie(self):
        foot = tk.Frame(self._sistema, bg=BG)
        foot.grid(row=3, column=0, sticky="ew", padx=44, pady=(0, 16))
        foot.grid_columnconfigure(0, weight=1)

        izq = tk.Frame(foot, bg=BG)
        izq.grid(row=0, column=0, sticky="w")
        self.lbl_terminal = tk.Label(izq, text="", font=F(10), fg=DIM2, bg=BG)
        self.lbl_terminal.pack(side="left", padx=(0, 18))
        self.lbl_ubicacion = tk.Label(izq, text="", font=F(10), fg=DIM2, bg=BG)
        self.lbl_ubicacion.pack(side="left")
        self._refrescar_ubicacion()
        self._refrescar_nombre_terminal()

        der = tk.Frame(foot, bg=BG)
        der.grid(row=0, column=1, sticky="e")
        # Cuatro accesos, cada uno abre solo lo suyo. Antes «Ajustes» era una
        # sola ventana con todo adentro y había que buscar la sección.
        accesos = (("🔌  Estado", self._abrir_estado),
                   ("🖥  Terminal", lambda: self._abrir_ajustes("terminal")),
                   ("⚙  Ajustes", lambda: self._abrir_ajustes("ajustes")),
                   ("▶  Pruebas", lambda: self._abrir_ajustes("pruebas")))
        for texto, accion in accesos:
            widgets.RoundedButton(der, texto, CARD, DIM, accion, BG,
                                  F(10, True), hover=CARD_BD, r=10, padx=12,
                                  pady=5, ancho=118)\
                .pack(side="left", padx=(0, 8))
        tk.Frame(der, bg=BG, width=8).pack(side="left")
        # Estado en línea: luz + texto + última conexión
        self.luz_online = self._crear_punto(der)
        self.luz_online.pack(side="left", padx=(0, 7))
        self.lbl_conexion = tk.Label(der, text="Bakelite: verificando…",
                                     font=F(10), fg=DIM, bg=BG)
        self.lbl_conexion.pack(side="left")
        # Segundo indicador: la API externa que dice si el RUT está habilitado.
        self.luz_externa = self._crear_punto(der)
        self.luz_externa.pack(side="left", padx=(18, 7))
        self.lbl_externa = tk.Label(der, text="API externa: verificando…",
                                    font=F(10), fg=DIM, bg=BG)
        self.lbl_externa.pack(side="left")

