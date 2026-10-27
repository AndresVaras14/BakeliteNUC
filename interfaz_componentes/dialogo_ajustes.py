# -*- coding: utf-8 -*-
"""Diálogo de ajustes locales del terminal."""

import interfaz as _base

globals().update({nombre: getattr(_base, nombre) for nombre in dir(_base)
                  if not nombre.startswith("__")})

class DialogoAjustesMixin:
    """Configuración de terminal, dispositivos y pruebas de hardware."""

    def _abrir_ajustes(self, clave="ajustes"):
        aj = getattr(self.controlador, "ajustes", None) if self.controlador else None
        if aj is None:
            return
        # Si ya hay una sección abierta, se reemplaza: dos ventanas de
        # configuración a la vez solo confunden.
        if self._dlg is not None and tk.Toplevel.winfo_exists(self._dlg):
            if getattr(self, "_dlg_seccion", None) == clave:
                self._dlg.lift()
                return
            self._dlg.destroy()

        titulo, bajada = self.SECCIONES[clave]
        constructor = {"terminal": self._tab_terminal,
                       "ajustes": self._tab_dispositivos,
                       "pruebas": self._tab_pruebas}[clave]

        dlg = tk.Toplevel(self.root, bg=BG)
        self._dlg = dlg
        self._dlg_seccion = clave
        dlg.title(titulo)
        dlg.configure(bg=BG)
        dlg.geometry("760x640")
        dlg.transient(self.root)
        dlg.resizable(False, False)

        cab = tk.Frame(dlg, bg=BG)
        cab.pack(fill="x", padx=26, pady=(20, 14))
        tk.Label(cab, text=titulo.upper(), font=F(16, True), fg=TXT, bg=BG)\
            .pack(anchor="w")
        tk.Label(cab, text=bajada, font=F(10), fg=DIM2, bg=BG)\
            .pack(anchor="w", pady=(2, 0))

        interno, contenedor = self._panel_scroll(dlg)
        contenedor.pack(fill="both", expand=True, padx=26)
        constructor(interno, aj)

        pie = tk.Frame(dlg, bg=BG)
        pie.pack(fill="x", padx=26, pady=(12, 18))
        widgets.RoundedButton(pie, "Cerrar", BG, TXT, dlg.destroy, BG,
                              F(10, True), hover=BG, r=12, padx=22, pady=9,
                              ancho=self.ANCHO_BOTON, borde=BORDE)\
            .pack(side="right")

    def _panel_scroll(self, parent):
        """Sección con scroll propio. Devuelve (contenido, contenedor)."""
        contenedor = tk.Frame(parent, bg=BG)

        lienzo = tk.Canvas(contenedor, bg=BG, highlightthickness=0)
        # La barra de scroll de Tk viene gris claro de fábrica y es lo único
        # que se sale del tema oscuro. Se pinta entera, incluido el canal.
        barra = tk.Scrollbar(contenedor, orient="vertical", command=lienzo.yview,
                             bg=CARD_BD, troughcolor=BG, activebackground=DIM2,
                             highlightthickness=0, borderwidth=0, relief="flat",
                             width=10)
        interno = tk.Frame(lienzo, bg=BG)
        ventana = lienzo.create_window((0, 0), window=interno, anchor="nw")
        lienzo.configure(yscrollcommand=barra.set)
        barra.pack(side="right", fill="y")
        lienzo.pack(side="left", fill="both", expand=True)

        def ajustar(evento=None):
            """La barra solo se muestra si el contenido no cabe. Una barra
            permanente sobre una sección corta es ruido: sugiere que hay algo
            más abajo cuando no lo hay."""
            caja = lienzo.bbox("all")
            lienzo.configure(scrollregion=caja or (0, 0, 0, 0))
            if evento is not None and evento.widget is lienzo:
                lienzo.itemconfig(ventana, width=evento.width)
            sobra = bool(caja) and caja[3] > lienzo.winfo_height()
            if sobra and not barra.winfo_ismapped():
                barra.pack(side="right", fill="y", before=lienzo)
            elif not sobra and barra.winfo_ismapped():
                barra.pack_forget()

        interno.bind("<Configure>", ajustar)
        lienzo.bind("<Configure>", ajustar)

        self._enganchar_rueda(contenedor, lienzo,
                              hay_scroll=contenedor.winfo_ismapped)
        return interno, contenedor

    def _titulo_seccion(self, parent, texto, ayuda=None):
        tk.Label(parent, text=texto.upper(), font=F(9, True), fg=DIM2, bg=BG)\
            .pack(anchor="w", padx=24, pady=(20, 2))
        if ayuda:
            tk.Label(parent, text=ayuda, font=F(9), fg=DIM2, bg=BG,
                     wraplength=640, justify="left").pack(anchor="w", padx=24,
                                                          pady=(0, 8))

    def _campo(self, parent, valor, on_guardar):
        """Entrada de texto con su botón Guardar al lado."""
        fila = tk.Frame(parent, bg=BG)
        fila.pack(fill="x", padx=24)
        ent = tk.Entry(fila, font=F(12), bg=CARD, fg=TXT, insertbackground=TXT,
                       relief="flat", bd=9)
        ent.pack(side="left", fill="x", expand=True, ipady=3)
        ent.insert(0, valor or "")
        widgets.RoundedButton(fila, "Guardar", GREEN, "#0c1626",
                              lambda: on_guardar(ent), BG, F(10, True),
                              hover="#54e08f", r=10, padx=16, pady=8,
                              ancho=self.ANCHO_BOTON)\
            .pack(side="left", padx=(10, 0))
        return ent

    def _selector_sentido(self, parent, on_elegir):
        """Dos opciones visibles, la activa resaltada. Devuelve (frame, pintar)."""
        marco = tk.Frame(parent, bg=BG)
        botones = {}
        for sentido, texto in (("E", "ENTRADA"), ("S", "SALIDA")):
            b = widgets.RoundedButton(marco, texto, BG, DIM2, None, BG,
                                      F(10, True), r=10, padx=14, pady=7,
                                      ancho=self.ANCHO_BOTON, borde=BORDE)
            b._command = lambda sen=sentido: on_elegir(sen)
            b.pack(side="left", padx=(0, 6))
            botones[sentido] = b

        def pintar(actual):
            """La activa se rellena; la otra queda como contorno, del color del
            panel. Así se ve cuál está puesta sin manchar la sección de color."""
            for sentido, b in botones.items():
                activo = sentido == actual
                color = GREEN if sentido == "E" else YELLOW
                b.set_style(fill=(color if activo else BG),
                            fg=("#0c1626" if activo else DIM2),
                            hover=(color if activo else BG),
                            borde=(None if activo else BORDE))

        return marco, pintar

    def _fila_aparato(self, parent, titulo, on_elegir, extra=None):
        """Una fila de la tabla: nombre + detalle a la izquierda, acciones a la
        derecha. `extra` agrega un botón antes del selector (ej: «Probar»)."""
        # Todo comparte el fondo del panel: la fila no es una tarjeta aparte,
        # es una línea de la misma sección. Solo un filete la separa de la
        # siguiente.
        marco = tk.Frame(parent, bg=BG, padx=8, pady=10)
        marco.pack(fill="x", padx=24, pady=(0, 6))

        izq = tk.Frame(marco, bg=BG)
        izq.pack(side="left")
        lbl = tk.Label(izq, text=titulo, font=F(12, True), fg=TXT, bg=BG)
        lbl.pack(anchor="w")
        sub = tk.Label(izq, text="", font=F(9), fg=DIM2, bg=BG)
        sub.pack(anchor="w", pady=(2, 0))

        selector, pintar = self._selector_sentido(marco, on_elegir)
        selector.pack(side="right")
        boton_extra = None
        if extra:
            boton_extra = widgets.RoundedButton(
                marco, extra[0], BG, DIM, extra[1], BG, F(10, True),
                hover=CARD, r=10, padx=14, pady=7, ancho=self.ANCHO_BOTON,
                borde=BORDE)
            boton_extra.pack(side="right", padx=(0, 12))
        return marco, sub, pintar, boton_extra, lbl

    def _encabezado_tabla(self, parent, izquierda, derecha):
        """Rótulos de columna: dicen qué se está mirando y qué se puede tocar."""
        fila = tk.Frame(parent, bg=BG)
        fila.pack(fill="x", padx=40, pady=(4, 0))
        tk.Label(fila, text=izquierda.upper(), font=F(8, True), fg=DIM2,
                 bg=BG).pack(side="left")
        tk.Label(fila, text=derecha.upper(), font=F(8, True), fg=DIM2,
                 bg=BG).pack(side="right")

    def _tab_terminal(self, cont, aj):
        self._titulo_seccion(
            cont, "Nombre del terminal",
            "Se sincroniza con Bakelite en los dos sentidos: gana el último "
            "cambio, venga de aquí o de la web.")
        estado = tk.Label(cont, text="", font=F(9), fg=DIM2, bg=BG,
                          wraplength=640, justify="left")

        def guardar_nombre(ent):
            nuevo = ent.get().strip()
            if not nuevo:
                estado.config(text="El nombre no puede quedar vacío.", fg=RED)
                return
            depurador.accion(f"Renombrar terminal a {nuevo!r}", origen="ajustes")
            quedo = self.controlador.renombrar_terminal(nuevo, usuario="operador")
            if quedo is None:
                estado.config(text="No se pudo guardar: la BD local no responde.",
                              fg=RED)
                return
            self._ui_nombre_terminal(quedo)
            estado.config(text="Guardado. Se sube a Bakelite en cuanto haya conexión.",
                          fg=GREEN)

        nombre = self.controlador.nombre_terminal() if self.controlador else ""
        self._entrada_nombre = self._campo(cont, nombre, guardar_nombre)
        estado.pack(anchor="w", padx=24, pady=(8, 0))

        self._titulo_seccion(cont, "Ubicación",
                             "Solo se muestra en la pantalla de este equipo.")
        estado_u = tk.Label(cont, text="", font=F(9), fg=DIM2, bg=BG)

        def guardar_ubic(ent):
            aj.ubicacion = ent.get().strip()
            aj.guardar()
            self._refrescar_ubicacion()
            estado_u.config(text="Ubicación guardada.", fg=GREEN)

        self._campo(cont, aj.ubicacion, guardar_ubic)
        estado_u.pack(anchor="w", padx=24, pady=(8, 20))

    def _tab_dispositivos(self, cont, aj):
        self._filas_lectoras = {}
        self._filas_reles = {}

        self._titulo_seccion(
            cont, "Lectoras",
            "Toca el botón de cada fila para cambiar su sentido: marcar una "
            "como ENTRADA deja la otra como SALIDA.")

        def refrescar_lectoras():
            """Lee el estado de AHORA: qué puerto tiene y si está enchufada. El
            último puerto guardado en la BD sirve de pista, pero si la lectora
            no está conectada decirlo a secas hace creer que sí lo está."""
            for n, (marco, sub, pintar, _lbl) in self._filas_lectoras.items():
                conectada = bool(self.estado_hw.get(f"lectora{n}"))
                puerto = self._puerto_lectora(n)
                if conectada and puerto:
                    sub.config(text=f"● conectada · {puerto}", fg=GREEN)
                else:
                    sub.config(text="● sin conectar", fg=RED)
                pintar(aj.sentido_lectora(n))

        self._refrescar_lectoras = refrescar_lectoras
        self._encabezado_tabla(cont, "dispositivo", "sentido")
        for n in (1, 2):
            def elegir(sentido, num=n):
                if aj.sentido_lectora(num) != sentido:
                    depurador.accion(
                        f"Lectora {num} pasa a {'ENTRADA' if sentido == 'E' else 'SALIDA'}",
                        origen="ajustes")
                    aj.set_sentido_lectora(num, sentido, usuario="operador")
                    refrescar_lectoras()
                    self.controlador.notificar_dispositivos()
                    # El aviso de arriba nombra las lectoras por su sentido:
                    # si cambia aquí, tiene que cambiar allá en el acto.
                    self._aplicar_estado(self.estado_hw)

            marco, sub, pintar, _, lbl = self._fila_aparato(
                cont, f"Lectora {n}", elegir)
            self._filas_lectoras[n] = (marco, sub, pintar, lbl)
        refrescar_lectoras()

        self._titulo_seccion(
            cont, "¿Cuál lectora es cuál?",
            "Las lectoras son escáneres: solo leen, no se pueden hacer "
            "parpadear. Aprieta el botón y pasa una cédula por la que quieras "
            "reconocer. Esa lectura no abre el torniquete ni queda registrada.")
        self._lbl_ident = tk.Label(cont, text="", font=F(10), fg=DIM2, bg=BG,
                                   wraplength=640, justify="left")

        def identificar():
            if self.controlador is None:
                return
            depurador.accion("Identificar lectora: esperando un escaneo",
                             origen="ajustes")
            self.controlador.iniciar_identificacion(
                lambda numero, rut: self.cola.put(
                    lambda: self._ui_lectora_identificada(numero, rut, aj)))
            self._lbl_ident.config(
                text="Esperando… pasa una cédula por la lectora que quieras reconocer.",
                fg=BLUE)
            for n, (marco, sub, pintar, lbl) in self._filas_lectoras.items():
                lbl.config(fg=TXT)

        widgets.RoundedButton(cont, "🔍  Identificar lectora", BG, TXT,
                              identificar, BG, F(10, True), hover=CARD,
                              r=10, padx=18, pady=8, ancho=self.ANCHO_BOTON * 2,
                              borde=BORDE)\
            .pack(anchor="w", padx=24)
        self._lbl_ident.pack(anchor="w", padx=24, pady=(10, 0))

        self._titulo_seccion(
            cont, "Relés (torniquetes)",
            "«Probar» acciona ese relé: mira cuál torniquete se abrió y márcalo "
            "como entrada o salida.")
        self._lbl_rele = tk.Label(cont, text="", font=F(10), fg=DIM2, bg=BG,
                                  wraplength=640, justify="left")

        def refrescar_reles():
            for n, (marco, sub, pintar, _p) in self._filas_reles.items():
                sub.config(text=f"comando: {aj.comando_de_rele(n)}")
                pintar(aj.reles.get(n, "E"))

        self._encabezado_tabla(cont, "dispositivo", "probar / sentido")
        for n in (1, 2):
            def elegir_rele(sentido, num=n):
                if aj.reles.get(num, "E") != sentido:
                    depurador.accion(
                        f"Relé {num} pasa a {'ENTRADA' if sentido == 'E' else 'SALIDA'}",
                        origen="ajustes")
                    aj.set_sentido_rele(num, sentido, usuario="operador")
                    refrescar_reles()
                    self.controlador.notificar_dispositivos()

            def probar_rele(num=n):
                if self.controlador is None:
                    return
                depurador.accion(f"Probar relé {num}", origen="ajustes")
                cmd = self.controlador.probar_rele_numero(num)
                self._lbl_rele.config(
                    text=f"Se accionó el relé {num} ({cmd}). "
                         "Mira qué torniquete se abrió.", fg=BLUE)

            marco, sub, pintar, probar, _ = self._fila_aparato(
                cont, f"Relé {n}", elegir_rele, extra=("▶  Probar", probar_rele))
            self._filas_reles[n] = (marco, sub, pintar, probar)
        refrescar_reles()
        self._lbl_rele.pack(anchor="w", padx=24, pady=(10, 0))

        self._titulo_seccion(
            cont, "Diagnóstico",
            "Parte la pantalla en dos y muestra, paso a paso, lo que se hace y "
            "lo que se recibe. El registro queda guardado en el equipo.")
        self._btn_debug = widgets.RoundedButton(
            cont, "🐞  Modo debugger", BG, TXT, self.alternar_debugger, BG,
            F(10, True), hover=BG, r=10, padx=18, pady=8,
            ancho=self.ANCHO_BOTON * 2, borde=BORDE)
        self._btn_debug.pack(anchor="w", padx=24, pady=(0, 20))

    def _tab_pruebas(self, cont, aj=None):
        self._titulo_seccion(
            cont, "Abrir torniquete",
            "Dispara el relé que hoy corresponde a cada sentido. Sirve para "
            "confirmar que la configuración quedó bien.")
        pr = tk.Frame(cont, bg=BG)
        pr.pack(anchor="w", padx=24)
        for texto, sentido in (("▶  ENTRADA", "E"), ("▶  SALIDA", "S")):
            widgets.RoundedButton(pr, texto, BG, TXT,
                                  lambda s=sentido: self.controlador.probar_rele(s),
                                  BG, F(10, True), hover=BG, r=10, padx=18,
                                  pady=8, ancho=self.ANCHO_BOTON, borde=BORDE)\
                .pack(side="left", padx=(0, 10))

        self._titulo_seccion(cont, "Luces del semáforo",
                             "Comprueba que el Arduino responde.")
        pl = tk.Frame(cont, bg=BG)
        pl.pack(anchor="w", padx=24)
        for texto, color, clave in (("Azul", BLUE, "azul"), ("Verde", GREEN, "verde"),
                                    ("Roja", RED, "rojo"),
                                    ("Amarilla", YELLOW, "amarillo")):
            widgets.RoundedButton(pl, texto, color, "#0c1626",
                                  lambda c=clave: self.controlador.probar_luz(c),
                                  BG, F(10, True), hover=color, r=10,
                                  padx=14, pady=7, ancho=self.ANCHO_BOTON)\
                .pack(side="left", padx=(0, 8))

        # Apagar no enciende nada: va aparte, sin color y en su propia línea.
        widgets.RoundedButton(cont, "Apagar", BG, TXT,
                              lambda: self.controlador.probar_luz("off"),
                              BG, F(10, True), hover=BG, r=10, padx=14, pady=7,
                              ancho=self.ANCHO_BOTON, borde=BORDE)\
            .pack(anchor="w", padx=24, pady=(10, 20))

