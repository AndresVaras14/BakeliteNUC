# -*- coding: utf-8 -*-
"""Reloj, recursos gráficos y ciclo final de la interfaz."""

import interfaz as _base

globals().update({nombre: getattr(_base, nombre) for nombre in dir(_base)
                  if not nombre.startswith("__")})

class RecursosMixin:
    """Recursos visuales, reloj, manejo de errores y bucle principal."""

    def _tick_reloj(self):
        now = datetime.datetime.now()
        self.lbl_hora.config(text=now.strftime("%H:%M:%S"))
        self.lbl_fecha.config(text=self._fecha(now))
        self.root.after(1000, self._tick_reloj)

    def _hora_hm(self, dt):
        """Formato horario visual único del NUC."""
        return dt.strftime("%H:%M:%S")

    def _fecha(self, dt):
        return f"{DIAS[dt.weekday()]}, {dt.day} De {MESES[dt.month - 1]} De {dt.year}"

    def _png_desde_pil(self, img):
        fd, ruta = tempfile.mkstemp(suffix=".png", prefix="ui_")
        os.close(fd)
        img.save(ruta)
        ph = tk.PhotoImage(file=ruta)
        try:
            os.remove(ruta)
        except OSError as e:
            log.debug("No se pudo eliminar el PNG temporal %s: %s", ruta, e)
        return ph

    def _preparar_badges(self):
        self._badge_img = {}
        self._badge_ok = False
        try:
            from PIL import Image, ImageDraw
        except Exception:  # noqa: BLE001
            return
        S, size = 4, 68
        D = size * S
        dark = (12, 22, 38, 255)

        def rgb(hx):
            hx = hx.lstrip("#")
            return tuple(int(hx[i:i + 2], 16) for i in (0, 2, 4)) + (255,)

        def punto(dr, p, w):
            dr.ellipse([p[0] - w / 2, p[1] - w / 2, p[0] + w / 2, p[1] + w / 2], fill=dark)

        def dibujar(color_hex, tipo):
            img = Image.new("RGBA", (D, D), (0, 0, 0, 0))
            dr = ImageDraw.Draw(img)
            dr.ellipse([2 * S, 2 * S, D - 2 * S, D - 2 * S], fill=rgb(color_hex))
            w = int(5.5 * S)
            if tipo == "ok":
                pts = [(0.30 * D, 0.52 * D), (0.44 * D, 0.66 * D), (0.72 * D, 0.35 * D)]
                dr.line(pts, fill=dark, width=w, joint="curve")
                for p in pts:
                    punto(dr, p, w)
            elif tipo == "no":
                a = [(0.35 * D, 0.35 * D), (0.65 * D, 0.65 * D)]
                b = [(0.65 * D, 0.35 * D), (0.35 * D, 0.65 * D)]
                dr.line(a, fill=dark, width=w)
                dr.line(b, fill=dark, width=w)
                for p in a + b:
                    punto(dr, p, w)
            else:
                dr.line([(0.5 * D, 0.28 * D), (0.5 * D, 0.56 * D)], fill=dark, width=w)
                punto(dr, (0.5 * D, 0.70 * D), w * 1.1)
            return self._png_desde_pil(img.resize((size, size), Image.LANCZOS))

        try:
            self._badge_img = {"ok": dibujar(GREEN, "ok"), "no": dibujar(RED, "no"),
                               "warn": dibujar(YELLOW, "warn")}
            self._badge_ok = True
        except Exception as e:  # noqa: BLE001
            log.error("No se pudieron generar los badges: %s", e)

    def _cargar_logo(self, ruta, alto, color=None):
        """Carga un logo y lo deja listo para el fondo oscuro: recorta el margen
        transparente, lo recolorea si hace falta (los logos de Bakelite vienen en
        azul oscuro, invisibles aquí) y lo escala al alto pedido.
        Devuelve None si falta el archivo o Pillow: la app sigue sin logo."""
        try:
            from PIL import Image
        except Exception:  # noqa: BLE001
            return None
        if not ruta or not os.path.exists(ruta):
            log.warning("Logo no encontrado: %s", ruta)
            return None
        try:
            img = Image.open(ruta).convert("RGBA")
            # Recorte por umbral de alfa: getbbox() no sirve aquí porque estos
            # archivos traen un halo casi invisible (alfa 1) que ocupa toda la
            # imagen, y al escalar dejaría el logo diminuto.
            caja = img.getchannel("A").point(lambda p: 255 if p > 8 else 0).getbbox()
            if caja:
                img = img.crop(caja)
            if color:
                # Se conserva la transparencia y se reemplaza el color: así el
                # logo se ve sobre el fondo oscuro sin perder el suavizado.
                r, g, b = (int(color.lstrip("#")[j:j + 2], 16) for j in (0, 2, 4))
                alfa = img.getchannel("A")
                plano = Image.new("RGBA", img.size, (r, g, b, 255))
                plano.putalpha(alfa)
                img = plano
            ancho = max(1, round(img.width * (alto / img.height)))
            return self._png_desde_pil(img.resize((ancho, alto), Image.LANCZOS))
        except Exception as e:  # noqa: BLE001
            log.error("No se pudo cargar el logo %s: %s", ruta, e)
            return None

    def _preparar_logos(self):
        self._logo_bakelite = self._cargar_logo(
            config.LOGO_BAKELITE, config.LOGO_BAKELITE_ALTO, color=TXT)
        self._logo_sopytec = self._cargar_logo(
            config.LOGO_SOPYTEC, config.LOGO_SOPYTEC_ALTO)

    def _preparar_puntos(self):
        """Puntos redondos de los indicadores de conexión. Se dibujan con Pillow
        a 8x y se reducen, así el borde queda suave: un óvalo de Canvas sale
        dentado en tamaños chicos."""
        self._punto_img = {}
        self._punto_ok = False
        try:
            from PIL import Image, ImageDraw
        except Exception:  # noqa: BLE001
            return
        S = 8

        def rgb(hx, a=255):
            hx = hx.lstrip("#")
            return tuple(int(hx[i:i + 2], 16) for i in (0, 2, 4)) + (a,)

        def punto(color_hex, size):
            D = size * S
            img = Image.new("RGBA", (D, D), (0, 0, 0, 0))
            dr = ImageDraw.Draw(img)
            dr.ellipse([0, 0, D - 1, D - 1], fill=rgb(color_hex, 55))     # halo
            m = int(D * 0.18)
            dr.ellipse([m, m, D - m, D - m], fill=rgb(color_hex, 255))    # núcleo
            return self._png_desde_pil(img.resize((size, size), Image.LANCZOS))

        try:
            for size in PUNTO_TAMANOS:
                for nombre, c in COLOR_PUNTO.items():
                    self._punto_img[(nombre, size)] = punto(c, size)
            self._punto_ok = True
        except Exception as e:  # noqa: BLE001
            log.error("No se pudieron generar los puntos de conexión: %s", e)

    def _crear_punto(self, parent, color="amarillo", tam=12, bg=None):
        """Indicador redondo, el mismo en toda la app. Con Pillow es una imagen
        suavizada; sin Pillow, un óvalo de Canvas como respaldo."""
        fondo = bg or BG
        if getattr(self, "_punto_ok", False) and (color, tam) in self._punto_img:
            lbl = tk.Label(parent, image=self._punto_img[(color, tam)], bg=fondo, bd=0)
            lbl._es_imagen = True
            lbl._tam = tam
            return lbl
        cv = tk.Canvas(parent, width=tam + 2, height=tam + 2, bg=fondo, highlightthickness=0)
        cv._oval = cv.create_oval(1, 1, tam + 1, tam + 1, fill=COLOR_PUNTO[color], outline="")
        cv._es_imagen = False
        cv._tam = tam
        return cv

    def _pintar_punto(self, punto, color):
        tam = getattr(punto, "_tam", 12)
        if getattr(punto, "_es_imagen", False):
            punto.config(image=self._punto_img[(color, tam)])
        else:
            punto.itemconfig(punto._oval, fill=COLOR_PUNTO[color])

    def _preparar_luces(self):
        """Luces del modo PC. Usan el mismo degradado que el torniquete: antes
        eran dos elipses (núcleo + halo plano) y se notaba el escalón."""
        self._luz_img = {}
        self._luz_ok = False
        try:
            for nombre in LUCES:
                img = self._imagen_luz(nombre, LUZ_PC_LADO)
                if img is None:
                    return
                self._luz_img[nombre] = img
            self._luz_ok = True
        except Exception as e:  # noqa: BLE001
            log.error("No se pudieron generar las luces: %s", e)

    def _mostrar_critico(self, texto):
        self.lbl_critico.config(text="  ⚠  ERROR CRÍTICO: " + str(texto)[:120] + "  ")
        self.lbl_critico.pack(in_=self._banner_cont, before=self._banner_b, pady=(0, 8))

    def _limpiar_critico(self):
        """El problema se resolvió: el cartel se va. Un aviso que no sabe
        desaparecer termina mintiendo apenas la causa se corrige."""
        self.lbl_critico.pack_forget()

    def _on_tk_error(self, exc, val, tb):
        logging.getLogger("errores").error("Excepción en callback de Tk: %s", val,
                                            exc_info=(exc, val, tb))
        try:
            self._mostrar_critico(str(val))
        except Exception:  # noqa: BLE001
            log.exception("No se pudo mostrar en pantalla la excepción de Tk.")

    def _toggle_fs(self, _e=None):
        actual = bool(self.root.attributes("-fullscreen"))
        self.root.attributes("-fullscreen", not actual)

    def _bind_sim(self):
        if not self.controlador:
            return
        ruts = ["4266307-7", "12329308-8", "9884029-K",
                "17346232-8", "20820085-2", "18419773-1"]
        for i, rut in enumerate(ruts, start=1):
            self.root.bind(str(i), lambda e, x=rut: self.controlador.simular(x, "E"))
            self.root.bind(f"<Control-Key-{i}>",
                           lambda e, x=rut: self.controlador.simular(x, "S"))
        self.root.bind("0", lambda e: self.controlador.simular("11111111-1", "E"))
        self.root.bind("<period>",
                       lambda e: self.controlador.simular("", "E", error_lectura=True))
        self.root.bind("<minus>",
                       lambda e: self.controlador.simular("4266307-7", "E", sin_conexion=True))

    def run(self):
        self.root.mainloop()

