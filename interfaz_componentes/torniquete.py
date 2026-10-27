# -*- coding: utf-8 -*-
"""Vista operativa de pantalla completa del torniquete."""

import interfaz as _base

globals().update({nombre: getattr(_base, nombre) for nombre in dir(_base)
                  if not nombre.startswith("__")})

class TorniqueteMixin:
    """Modo torniquete, semáforo y último acceso."""

    def set_modo(self, modo):
        if modo not in ("pc", "torniquete") or modo == self.modo:
            return
        self.modo = modo
        depurador.accion(f"Cambio a modo {modo.upper()}", origen="interfaz")
        if modo == "torniquete":
            for zona in self._zonas_pc:
                zona.grid_remove()
            if self._vista_torniquete is None:
                self._construir_torniquete()
            self._vista_torniquete.grid()
            self._sincronizar_torniquete()
        else:
            if self._vista_torniquete is not None:
                self._vista_torniquete.grid_remove()
            for zona in self._zonas_pc:
                zona.grid()
        self._pintar_modo()
        # Cambió el contenido: el contenedor tiene que recalcular su alto.
        self.root.after_idle(self._ajustar_sistema)

    def _pintar_modo(self):
        for modo, b in getattr(self, "_btn_modo", {}).items():
            activo = modo == self.modo
            b.set_style(fill=(BLUE if activo else BG),
                        fg=("#0c1626" if activo else DIM2),
                        hover=(BLUE if activo else BG),
                        borde=(None if activo else BORDE))

    def _construir_torniquete(self):
        """Vista grande: los datos a la izquierda, la luz a la derecha (40%).

        Es la pantalla que ve quien está pasando, en un monitor de 800 a 1024
        px. Solo lo indispensable: si puede pasar, quién es, y si el sistema
        está en línea.
        """
        v = tk.Frame(self._sistema, bg=BG)
        v.grid(row=1, column=0, rowspan=3, sticky="nsew", padx=26, pady=(2, 8))
        v.grid_rowconfigure(0, weight=1)
        # `uniform` es lo que hace que la proporción se cumpla de verdad: los
        # pesos por sí solos reparten el espacio SOBRANTE, y como el bloque de
        # datos pide bastante ancho, la luz terminaba en un 30%.
        v.grid_columnconfigure(0, weight=6, uniform="torniquete")   # datos
        v.grid_columnconfigure(1, weight=4, uniform="torniquete")   # luz: 40%
        self._vista_torniquete = v

        # --- Izquierda: estado, quién pasa y el último acceso ---
        izq = tk.Frame(v, bg=BG)
        izq.grid(row=0, column=0, sticky="nsew", padx=(0, 20))
        izq.grid_rowconfigure(0, weight=1)
        izq.grid_columnconfigure(0, weight=1)

        actual = tk.Frame(izq, bg=BG)
        actual.grid(row=0, column=0, sticky="nsew")
        self.t_banner = tk.Label(actual, text=ESPERA_TITULO, font=F(24, True),
                                 fg=GREEN, bg=BG, justify="left", anchor="w")
        self.t_banner.pack(anchor="w", fill="x")
        self.t_sub = tk.Label(actual, text=ESPERA_AYUDA, font=F(15), fg=DIM,
                              bg=BG, justify="left", anchor="w")
        self.t_sub.pack(anchor="w", fill="x", pady=(6, 0))
        self.t_nombre = tk.Label(actual, text="", font=F(34, True), fg=TXT,
                                 bg=BG, justify="left", anchor="w")
        self.t_nombre.pack(anchor="w", fill="x", pady=(8, 0))
        self.t_cedula = tk.Label(actual, text="", font=F(19), fg=DIM, bg=BG,
                                 anchor="w")
        self.t_cedula.pack(anchor="w", fill="x", pady=(4, 0))

        fila = tk.Frame(actual, bg=BG)
        fila.pack(anchor="w", pady=(10, 0))
        # Se crean pero no se muestran: una píldora vacía se ve como un botón
        # suelto. Aparecen recién cuando hay un evento que mostrar.
        self.t_sentido = widgets.make_pill(fila, "", GREEN, "#0c1626", BG,
                                           F(15, True), padx=16, pady=5,
                                           r=RADIO_PASTILLA)
        self.t_hora = tk.Label(fila, text="", font=F(15), fg=DIM2, bg=BG)
        self.t_hora.pack(side="left")

        # --- Abajo: el último acceso y el estado de los servicios ---
        pie = tk.Frame(izq, bg=BG)
        pie.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        tk.Label(pie, text="ÚLTIMO ACCESO", font=F(10, True), fg=DIM2, bg=BG,
                 anchor="w").pack(anchor="w")
        self.t_ult_nombre = tk.Label(pie, text="—", font=F(18, True), fg=DIM,
                                     bg=BG, anchor="w")
        self.t_ult_nombre.pack(anchor="w", fill="x", pady=(4, 0))
        fila_ult = tk.Frame(pie, bg=BG)
        fila_ult.pack(anchor="w", pady=(4, 0))
        self.t_ult_cedula = tk.Label(fila_ult, text="", font=F(12), fg=DIM2, bg=BG)
        self.t_ult_cedula.pack(side="left", padx=(0, 14))
        self.t_ult_sentido = widgets.make_pill(fila_ult, "", CARD_BD, TXT, BG,
                                               F(11, True), padx=12, pady=4,
                                               r=RADIO_PASTILLA)
        # El resultado del acceso guardado, con su color: de un vistazo se sabe
        # si la última persona pasó o quedó fuera, sin leer nada. También nace
        # oculto: al arrancar todavía no pasó nadie.
        self.t_ult_veredicto = widgets.make_pill(fila_ult, "", CARD_BD, TXT, BG,
                                                 F(11, True), padx=12, pady=4,
                                                 r=RADIO_PASTILLA)
        self.t_ult_hora = tk.Label(fila_ult, text="", font=F(12), fg=DIM2, bg=BG)
        self.t_ult_hora.pack(side="left")

        # Estado de los servicios: si el sistema no puede validar, quien está
        # frente al torniquete merece saberlo antes de apoyar la cédula.
        estado = tk.Frame(izq, bg=BG)
        estado.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.t_luz_bakelite = self._crear_punto(estado, "amarillo", tam=11)
        self.t_luz_bakelite.pack(side="left", padx=(0, 7))
        self.t_srv_bakelite = tk.Label(estado, text="Bakelite: verificando…",
                                       font=F(11), fg=DIM, bg=BG)
        self.t_srv_bakelite.pack(side="left", padx=(0, 22))
        self.t_luz_externa = self._crear_punto(estado, "amarillo", tam=11)
        self.t_luz_externa.pack(side="left", padx=(0, 7))
        self.t_srv_externa = tk.Label(estado, text="API externa: verificando…",
                                      font=F(11), fg=DIM, bg=BG)
        self.t_srv_externa.pack(side="left")

        # --- Derecha: la luz con degradado, como en modo PC ---
        der = tk.Frame(v, bg=BG)
        der.grid(row=0, column=1, sticky="nsew")
        # Mínimo chico: la luz crece con el espacio disponible, pero no debe
        # empujar la vista más allá de la pantalla en un monitor de 600 px.
        self.t_luz = tk.Canvas(der, bg=BG, highlightthickness=0,
                               width=160, height=160)
        self.t_luz.pack(fill="both", expand=True)
        self._t_img_luz = None
        self._t_item_luz = None
        self._t_lado = 0
        self._t_color = "off"

        def redibujar(evento):
            lado = max(min(evento.width, evento.height) - 12, 90)
            self._t_dibujar_luz(lado, evento.width, evento.height)

        self.t_luz.bind("<Configure>", redibujar)

        self.t_estado = tk.Label(der, text=LUCES["off"][1], font=F(22, True),
                                 fg=DIM, bg=BG)
        self.t_estado.pack(pady=(0, 4))

        # Tamaños de referencia de cada texto, sobre una pantalla de 1024x600.
        # Se reescalan con el tamaño real: en un monitor grande la letra crece,
        # que es lo que hace que se lea desde lejos.
        self._t_fuentes = (
            (self.t_banner, 24, True), (self.t_sub, 15, False),
            (self.t_nombre, 34, True), (self.t_cedula, 19, False),
            (self.t_hora, 15, False),
            (self.t_ult_nombre, 18, True), (self.t_ult_cedula, 12, False),
            (self.t_ult_hora, 12, False),
            (self.t_srv_bakelite, 11, False), (self.t_srv_externa, 11, False),
        )

        def ajustar(evento):
            margen = max(int(evento.width * 0.58), 220)
            for w in (self.t_banner, self.t_sub, self.t_nombre, self.t_ult_nombre):
                w.config(wraplength=margen)
            escala = self._escala_torniquete(evento.width, evento.height)
            if abs(escala - self._t_escala) < 0.04:
                return                      # cambio insignificante: no repinta
            self._t_escala = escala
            for widget, base, negrita in self._t_fuentes:
                widget.config(font=(_base.FAM, max(int(base * escala), 8),
                                    "bold" if negrita else "normal"))
            self._t_pintar_veredicto()
            # Las píldoras son imágenes: cambiar la fuente no las redimensiona,
            # hay que volver a generarlas con el tamaño nuevo.
            if self._t_ultimo:
                self._t_pintar_ultimo(self._t_ultimo)

        v.bind("<Configure>", ajustar)
        self._t_limpiar()

    @staticmethod
    def _escala_torniquete(ancho, alto):
        """Factor de tamaño de letra según el espacio real de la vista.

        La referencia es 1024x600 —la pantalla típica del torniquete—: ahí el
        factor es 1. En un monitor grande crece y en uno chico se achica, con
        topes para que no quede ilegible ni desbordado.
        """
        factor = min(ancho / 980.0, alto / 560.0)
        return max(0.72, min(2.2, round(factor, 2)))

    def _t_dibujar_luz(self, lado, ancho, alto):
        """Redibuja la luz al tamaño disponible, con degradado hacia afuera."""
        self._t_lado = lado
        img = self._imagen_luz(self._t_color, lado)
        self.t_luz.delete("luz")
        if img is None:
            # Sin Pillow: círculo plano, mejor eso que nada.
            c, _e, _s = LUCES.get(self._t_color, LUCES["off"])
            apagada = self._t_color == "off"
            self.t_luz.create_oval(ancho / 2 - lado / 2, alto / 2 - lado / 2,
                                   ancho / 2 + lado / 2, alto / 2 + lado / 2,
                                   fill=(CARD if apagada else c),
                                   outline=(CARD_BD if apagada else c), width=3,
                                   tags="luz")
            return
        self._t_img_luz = img          # se guarda: Tk no retiene la imagen
        self.t_luz.create_image(ancho / 2, alto / 2, image=img, tags="luz")

    def _imagen_luz(self, color, lado):
        """Halo radial: el núcleo sólido y la caída suave hacia el borde.

        El modo PC usa dos elipses (núcleo + halo) sobre una imagen fija de
        150 px. Aquí la luz cambia de tamaño con la ventana, así que se genera
        a demanda y con más pasos, que es lo que da el degradado parejo.
        """
        clave = (color, lado)
        cache = self._cache_luz
        if clave in cache:
            return cache[clave]
        try:
            from PIL import Image, ImageDraw
        except Exception:  # noqa: BLE001
            cache[clave] = None
            return None
        try:
            hexa = CARD if color == "off" else LUCES.get(color, LUCES["off"])[0]
            r, g, b = (int(hexa.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
            S = 2
            D = lado * S
            img = Image.new("RGBA", (D, D), (0, 0, 0, 0))
            dr = ImageDraw.Draw(img)
            pasos = 120
            nucleo = 0.42          # hasta aquí, color pleno
            alfa_halo = 0.60 if color != "off" else 0.35
            # De afuera hacia adentro: cada anillo pisa al anterior, y la
            # secuencia de alfas crecientes deja el degradado.
            for i in range(pasos, 0, -1):
                f = i / pasos
                if f <= nucleo:
                    a = 255
                else:
                    t = (f - nucleo) / (1 - nucleo)
                    a = int(255 * alfa_halo * (1 - t) ** 2.4)
                if a <= 0:
                    continue
                radio = (D / 2) * f
                dr.ellipse([D / 2 - radio, D / 2 - radio,
                            D / 2 + radio, D / 2 + radio], fill=(r, g, b, a))
            foto = self._png_desde_pil(img.resize((lado, lado), Image.LANCZOS))
        except Exception as e:  # noqa: BLE001
            log.error("No se pudo generar la luz del torniquete: %s", e)
            foto = None
        cache[clave] = foto
        return foto

    def _texto_brillante(self, texto, color, tam):
        """El veredicto como imagen con halo: se lee desde lejos.

        Un Label normal se pierde contra el fondo oscuro a varios metros. Aquí
        el texto se dibuja dos veces —una desenfocada detrás, a modo de
        resplandor, y otra nítida encima—, que es lo que le da el brillo.
        """
        clave = (texto, color, tam)
        if clave in self._cache_brillo:
            return self._cache_brillo[clave]
        if not FUENTE_TTF:
            self._cache_brillo[clave] = None
            return None
        try:
            from PIL import Image, ImageDraw, ImageFont, ImageFilter
            fuente = ImageFont.truetype(FUENTE_TTF, tam)
            r, g, b = (int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
            margen = int(tam * 0.9)
            caja = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textbbox(
                (0, 0), texto, font=fuente)
            ancho = caja[2] - caja[0] + margen * 2
            alto = caja[3] - caja[1] + margen * 2
            img = Image.new("RGBA", (ancho, alto), (0, 0, 0, 0))
            dr = ImageDraw.Draw(img)
            pos = (margen - caja[0], margen - caja[1])
            dr.text(pos, texto, font=fuente, fill=(r, g, b, 190))
            halo = img.filter(ImageFilter.GaussianBlur(tam * 0.28))
            # El halo se refuerza para que el resplandor tenga cuerpo.
            halo = Image.alpha_composite(halo, halo)
            dr2 = ImageDraw.Draw(halo)
            dr2.text(pos, texto, font=fuente, fill=(r, g, b, 255))
            foto = self._png_desde_pil(halo)
        except Exception as e:  # noqa: BLE001
            log.error("No se pudo generar el texto brillante: %s", e)
            foto = None
        self._cache_brillo[clave] = foto
        return foto

    def _t_pintar_veredicto(self):
        """Redibuja el veredicto con el tamaño y color que correspondan."""
        c, etq, _sig = LUCES.get(self._t_color, LUCES["off"])
        color = DIM if self._t_color == "off" else c
        tam = max(int(self._t_escala * 30), 14)
        img = self._texto_brillante(etq, color, tam)
        if img is None:
            self.t_estado.config(image="", text=etq, fg=color,
                                 font=(_base.FAM, tam, "bold"))
            return
        self._t_img_estado = img          # Tk no retiene la imagen
        self.t_estado.config(image=img, text="")

    def _t_pintar_luz(self, color):
        self._t_color = color
        self._t_pintar_veredicto()
        if self._t_lado:
            self._t_dibujar_luz(self._t_lado, self.t_luz.winfo_width(),
                                self.t_luz.winfo_height())

    def _sincronizar_torniquete(self):
        """Al entrar al modo, refleja lo que la pantalla ya venía mostrando."""
        if self._vista_torniquete is None:
            return
        self.t_banner.config(text=self.lbl_estado.cget("text"),
                             fg=self.lbl_estado.cget("fg"))
        if self._t_ultimo:
            self._t_pintar_ultimo(self._t_ultimo)
        elif self.historial:
            self._t_pintar_ultimo(self.historial[0])
        for servicio in ("bakelite", "externa"):
            datos = self._estado_servicios.get(servicio)
            if datos:
                self._t_servicio(servicio, *datos)

    def _t_resultado(self, r):
        """Quién está pasando, en grande; y abajo, ese mismo acceso como el
        último registrado. Cuando la pantalla vuelva a "en espera", abajo queda
        quien acaba de pasar."""
        nombre = (r.nombre or "").strip() or "—"
        entrada = r.sentido == "E"
        hora = datetime.datetime.now().strftime("%H:%M:%S")
        rut_visual = _rut_visual(r.rut_display or r.rut_norm)
        self.t_sub.config(text="")
        self.t_nombre.config(text=nombre)
        self.t_cedula.config(text=(f"Cédula: {rut_visual}" if rut_visual else ""))
        widgets.set_pill(self.t_sentido, "ENTRADA" if entrada else "SALIDA",
                         GREEN if entrada else BLUE, "#0c1626", BG,
                         self._t_fuente(15, True), padx=16, pady=5,
                         r=RADIO_PASTILLA)
        if not self.t_sentido.winfo_ismapped():
            self.t_sentido.pack(side="left", padx=(0, 14), before=self.t_hora)
        self.t_hora.config(text=hora)

        # Solo los accesos de una persona identificada pasan al pie: un error
        # de lectura no reemplaza a quien sí pasó.
        if r.codigo in (0, 1):
            self._t_pintar_ultimo({"nombre": nombre, "rut": rut_visual,
                                   "sentido": "ENTRADA" if entrada else "SALIDA",
                                   "hora": hora, "codigo": r.codigo})

    def _t_pintar_ultimo(self, datos):
        self._t_ultimo = datos
        self.t_ult_nombre.config(text=datos.get("nombre") or "—")
        self.t_ult_cedula.config(text=_rut_visual(
            datos.get("rut") or datos.get("rut_display"),
        ))
        self.t_ult_hora.config(text=datos.get("hora") or "")

        entrada = datos.get("sentido") == "ENTRADA"
        fuente = self._t_fuente(11, True)
        widgets.set_pill(self.t_ult_sentido, datos.get("sentido") or "",
                         GREEN if entrada else BLUE, "#0c1626", BG, fuente,
                         padx=12, pady=4, r=RADIO_PASTILLA)
        if not self.t_ult_sentido.winfo_ismapped():
            self.t_ult_sentido.pack(side="left", padx=(0, 10),
                                    before=self.t_ult_hora)
        # El color del veredicto es el mismo que tuvo la luz: verde si pasó,
        # rojo si quedó fuera, amarillo si no se pudo saber.
        texto, color = VEREDICTO.get(datos.get("codigo"), ("", CARD_BD))
        if texto:
            widgets.set_pill(self.t_ult_veredicto, texto, color, "#0c1626", BG,
                             fuente, padx=12, pady=4, r=RADIO_PASTILLA)
            if not self.t_ult_veredicto.winfo_ismapped():
                self.t_ult_veredicto.pack(side="left", padx=(0, 14),
                                          before=self.t_ult_hora)
        else:
            self.t_ult_veredicto.pack_forget()

    def _t_fuente(self, base, negrita=False):
        """Tamaño ya escalado, para las píldoras que se dibujan como imagen."""
        return (_base.FAM, max(int(base * self._t_escala), 8),
                "bold" if negrita else "normal")

    def _t_limpiar(self):
        """Vuelve al mensaje de espera sin borrar el último acceso."""
        self.t_banner.config(text=ESPERA_TITULO, fg=GREEN)
        self.t_sub.config(text=ESPERA_AYUDA, fg=DIM)
        self.t_nombre.config(text="")
        self.t_cedula.config(text="")
        # Se oculta, no se vacía: una píldora sin texto igual dibuja su fondo
        # redondeado y queda como un botón vacío en medio de la pantalla.
        self.t_sentido.pack_forget()
        self.t_hora.config(text="")

    def _t_servicio(self, servicio, en_linea, texto):
        if self._vista_torniquete is None or not hasattr(self, "t_srv_bakelite"):
            return
        if servicio == "externa":
            punto, lbl = self.t_luz_externa, self.t_srv_externa
        else:
            punto, lbl = self.t_luz_bakelite, self.t_srv_bakelite
        color = "amarillo" if en_linea is None else ("verde" if en_linea else "rojo")
        self._pintar_punto(punto, color)
        lbl.config(text=texto, fg=(DIM if en_linea is not False else RED))

