# -*- coding: utf-8 -*-
"""
Interfaz gráfica (Tkinter) — estilo BAKELITE / CONTROL DE ACCESO.

Incluye: reloj (am/pm junto a la hora), luz grande en vivo + leyenda de colores,
tarjeta "ÚLTIMO REGISTRO", lista de los últimos 5 registros, pantalla de estado
de conexión, ajustes (invertir lectoras/relés, probar relé/luces, ubicación),
estado "en línea" con luz y hora de última conexión, y banner de error crítico.

Esquinas redondeadas vía widgets.py (Pillow). A prueba de errores: las
excepciones en callbacks de Tk se registran y muestran, sin tumbar la app.

Thread-safe: los hilos llaman a los métodos públicos (mostrar_*/set_*), que
encolan el cambio; el bucle de Tk lo aplica en el hilo principal.
"""

import os
import queue
import logging
import datetime
import tempfile
import threading
import tkinter as tk
import tkinter.font as tkfont

import config
from depurador import depurador
import widgets
from rut import formatea_rut

log = logging.getLogger("ui")

DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
         "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]

# ---- Paleta ----
BG = "#0f1c33"
CARD = "#182a45"
CARD_BD = "#2b4066"
# Filete y contornos: apenas se despega del panel, lo justo para separar.
BORDE = "#24395e"
ROW_BG = "#14243d"   # (sin uso: las filas del historial van sobre el fondo del panel)
TXT = "#e8eef7"
DIM = "#8ea3c0"
DIM2 = "#5d7290"
GREEN = "#2fce7c"
BLUE = "#3aa0ff"
RED = "#f0554f"
YELLOW = "#f6c344"
OFFC = "#33425c"

FAM = "DejaVu Sans"
FUENTE_TTF = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

CARD_W, CARD_H = 860, 168
HIST_W, HIST_H = 860, 300
NOMBRE_MAX = 24
NOMBRE_HIST_MAX = 22
MOTIVO_HIST_MAX = 26        # motivo del rechazo en la tabla acumulada
HIST_FILAS = 5              # filas fijas de la tabla (no se recrean)
# Lado de la luz del modo PC. El tope lo pone la columna, que mide 250 px por
# el panel de la leyenda: más que eso ensancharía toda la fila.
LUZ_PC_LADO = 236

# Mensaje de reposo del modo torniquete. Dos líneas: la primera dice qué hacer,
# la segunda por qué. "EN ESPERA" a secas no le decía nada a quien llega.
# Cómo se ve cada código de respuesta: rótulo corto y color. Se usa tanto en el
# evento actual como en el que queda guardado abajo.
VEREDICTO = {
    1: ("AUTORIZADO", GREEN),
    0: ("DENEGADO", RED),
    2: ("ERROR DE LECTURA", RED),
    3: ("ERROR DE LECTURA", RED),
    4: ("SIN CONEXIÓN", YELLOW),
    5: ("SIN RESPUESTA", YELLOW),
}
RADIO_PASTILLA = 8          # "un poco redondeado", no una cápsula

ESPERA_TITULO = "ACERQUE SU CÉDULA AL LECTOR"
ESPERA_AYUDA = "El sistema está listo y esperando a que pase su cédula de identidad."
# El más reciente se muestra en el panel grande "ÚLTIMO REGISTRO", así que la
# tabla arranca en el segundo. Se guarda uno más para no perder ninguno.
HIST_MEMORIA = HIST_FILAS + 1

# color de luz -> (color, etiqueta, significado)
# Puntos redondos: un único estilo para todos los indicadores de la app.
COLOR_PUNTO = {"verde": GREEN, "rojo": RED, "amarillo": YELLOW, "azul": BLUE}
PUNTO_TAMANOS = (12, 14, 16)


def _nombre_color(hexa):
    """Color hex -> nombre de punto, para reusar los mismos indicadores."""
    for nombre, c in COLOR_PUNTO.items():
        if c == hexa:
            return nombre
    return "amarillo"

LUCES = {
    "azul": (BLUE, "LEYENDO", "Leyendo / consultando"),
    "verde": (GREEN, "AUTORIZADO", "Acceso autorizado"),
    "rojo": (RED, "DENEGADO", "Denegado / error de lectura"),
    "amarillo": (YELLOW, "SIN CONEXIÓN", "Sin conexión a la red"),
    "off": (OFFC, "EN ESPERA", "En espera"),
}


def F(size, bold=False):
    return (FAM, size, "bold" if bold else "normal")


def recorta(texto, n):
    texto = (texto or "").strip() or "—"
    return texto if len(texto) <= n else texto[:n - 1] + "…"


class Interfaz:
    def __init__(self, controlador=None, sim=False, estado_hw=None, redetectar=None):
        self.controlador = controlador
        self.sim = sim
        self.redetectar_cb = redetectar
        self.estado_hw = estado_hw or {"arduino": False, "lectora1": False, "lectora2": False}
        self.cola = queue.Queue()
        self.historial = []
        self._dlg = None
        self._dlg_estado = None
        self._entrada_nombre = None
        self._dlg_seccion = None
        self.modo = "pc"
        self._vista_torniquete = None
        self._t_ultimo = None          # último acceso mostrado abajo
        self._t_escala = 1.0           # factor de tamaño de letra del torniquete
        self._cache_luz = {}           # (color, lado) -> imagen ya generada
        self._cache_brillo = {}        # (texto, color, tam) -> texto con halo
        self._estado_servicios = {}    # servicio -> (en_linea, texto)
        self._zonas_pc = []
        self.debug_activo = False
        self._panel_debug = None
        self._texto_debug = None

        self.root = tk.Tk()
        self.root.title(f"{config.MARCA} — {config.APP_TITULO}")
        self.root.configure(bg=BG)
        self.root.geometry("1440x860")
        # El mínimo tiene que caber en la pantalla del torniquete (800x600).
        # El modo PC conserva su diseño: si la ventana queda chica, el
        # contenedor con scroll se encarga.
        self.root.minsize(800, 600)
        self.root.report_callback_exception = self._on_tk_error
        try:
            self.root.attributes("-fullscreen", True)
        except Exception:  # noqa: BLE001
            pass
        self.root.bind("<Escape>", lambda e: self.root.attributes("-fullscreen", False))
        self.root.bind("<F11>", self._toggle_fs)
        self.root.bind("<F2>", lambda e: self._abrir_ajustes("ajustes"))

        self._preparar_badges()
        self._preparar_luces()
        self._preparar_puntos()
        self._preparar_logos()
        self._construir()
        self._bind_sim()

        self._tick_reloj()
        self._render_historial()
        self._aplicar_estado(self.estado_hw)
        self.set_luz("off")
        self.root.after(50, self._drenar_cola)
        self.mostrar_esperando()

        # El equipo arranca como pantalla de torniquete: es su uso normal. El
        # modo PC queda a un clic para operar o configurar.
        self.set_modo("torniquete")

        if not self.sim and not all(self.estado_hw.values()):
            self.root.after(400, self._abrir_estado)

    # ================= construcción =================
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

    RUEDA = ("<MouseWheel>", "<Button-4>", "<Button-5>")

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

    # ================= Modo de pantalla =================
    # "pc" es la vista completa de siempre. "torniquete" deja solo lo que le
    # sirve a quien está pasando: la luz y su resultado, en grande, para una
    # pantalla de 800 a 1024 px de ancho.
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
                widget.config(font=(FAM, max(int(base * escala), 8),
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

    # ---- La luz ----
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
                                 font=(FAM, tam, "bold"))
            return
        self._t_img_estado = img          # Tk no retiene la imagen
        self.t_estado.config(image=img, text="")

    def _t_pintar_luz(self, color):
        self._t_color = color
        self._t_pintar_veredicto()
        if self._t_lado:
            self._t_dibujar_luz(self._t_lado, self.t_luz.winfo_width(),
                                self.t_luz.winfo_height())

    # ---- Contenido ----
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
        self.t_sub.config(text="")
        self.t_nombre.config(text=nombre)
        self.t_cedula.config(text=(f"Cédula: {r.rut_display}" if r.rut_display else ""))
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
            self._t_pintar_ultimo({"nombre": nombre, "rut": r.rut_display,
                                   "sentido": "ENTRADA" if entrada else "SALIDA",
                                   "hora": hora, "codigo": r.codigo})

    def _t_pintar_ultimo(self, datos):
        self._t_ultimo = datos
        self.t_ult_nombre.config(text=datos.get("nombre") or "—")
        self.t_ult_cedula.config(text=datos.get("rut") or datos.get("rut_display") or "")
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
        return (FAM, max(int(base * self._t_escala), 8),
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

    # ================= Modo debugger =================
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
            self._circ_lbl = tk.Label(self.badge, text="●", font=(FAM, 62), fg=CARD, bg=CARD)
            self._circ_lbl.place(relx=0.5, rely=0.5, anchor="center")
            self._sym_lbl = tk.Label(self.badge, text="", font=(FAM, 20, "bold"),
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

    # ================= API pública (thread-safe) =================
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
            except Exception:  # noqa: BLE001
                pass

    def set_nombre_terminal(self, nombre):
        """El nombre cambió en Bakelite y el sincronizador lo adoptó. Llega
        desde el hilo del sincronizador, así que pasa por la cola."""
        self.cola.put(lambda: self._ui_nombre_terminal(nombre))

    # ================= aplicación en el hilo de Tk =================
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
            except Exception:  # noqa: BLE001
                pass
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
        self.lbl_cedula.config(text=f"Cédula: {r.rut_display or '—'}")
        self.lbl_hora_reg.config(text=self._hora_hm(datetime.datetime.now()))
        if r.sentido == "E":
            widgets.set_pill(self.lbl_tag, "ENTRADA", GREEN, "#0c1626", CARD, F(10, True),
                             padx=14, pady=4)
        else:
            widgets.set_pill(self.lbl_tag, "SALIDA", BLUE, "#0c1626", CARD, F(10, True),
                             padx=14, pady=4)

    # ================= historial (últimos 5) =================
    def _ui_cargar_historial(self, marcas):
        self.historial = []
        for m in marcas[:HIST_MEMORIA]:
            self.historial.append({
                "hora": self._hora_hm(m.get("fecha")) if m.get("fecha") else "—",
                "nombre": m.get("nombre") or "Desconocido",
                "rut": formatea_rut(m.get("rut") or "") or (m.get("rut") or "—"),
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
        self.lbl_cedula.config(text=f"Cédula: {ev['rut'] or '—'}")
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
            "rut": r.rut_display or "—",
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

    # ================= estado de conexión del hardware =================
    def _sentido_lectora(self, numero):
        """ENTRADA o SALIDA según la configuración vigente, no según el config.py.
        Antes estaba escrito a mano en cada texto y quedaba mintiendo apenas el
        operador cambiaba el sentido en Ajustes."""
        aj = getattr(self.controlador, "ajustes", None) if self.controlador else None
        if aj is None:
            nominal = config.SENTIDO_LECTORA1 if numero == 1 else config.SENTIDO_LECTORA2
            return "entrada" if nominal == "E" else "salida"
        return "entrada" if aj.sentido_lectora(numero) == "E" else "salida"

    def _puerto_lectora(self, numero):
        """Puerto real de ahora mismo, o None si está desconectada."""
        return (self.estado_hw.get("puertos") or {}).get(f"lectora{numero}")

    def _aplicar_estado(self, estado):
        self.estado_hw = dict(estado)
        faltan = []
        for n in (1, 2):
            if not estado.get(f"lectora{n}"):
                faltan.append(f"Lectora {n} ({self._sentido_lectora(n)})")
        if not estado.get("arduino"):
            faltan.append("Arduino (relés/luces)")
        if faltan:
            self.lbl_aviso.config(
                text="⚠  Falta conectar:  " + "   ·   ".join(faltan) + "     (toca para ver)")
        else:
            self.lbl_aviso.config(text="")
        if self._dlg_estado is not None and tk.Toplevel.winfo_exists(self._dlg_estado):
            self._render_estado_items()
        # Si Ajustes está abierto, sus filas también reflejan el cambio al vuelo.
        refrescar = getattr(self, "_refrescar_lectoras", None)
        if refrescar and self._dlg is not None and tk.Toplevel.winfo_exists(self._dlg):
            try:
                refrescar()
            except Exception:  # noqa: BLE001  el diálogo pudo cerrarse mientras tanto
                pass

    def _abrir_estado(self):
        if self._dlg_estado is not None and tk.Toplevel.winfo_exists(self._dlg_estado):
            self._dlg_estado.lift()
            return
        dlg = tk.Toplevel(self.root, bg=BG)
        self._dlg_estado = dlg
        dlg.title("Estado de conexión")
        dlg.configure(bg=BG)
        dlg.geometry("560x400")
        dlg.transient(self.root)
        dlg.resizable(False, False)
        tk.Label(dlg, text="ESTADO DE CONEXIÓN", font=F(15, True), fg=TXT, bg=BG).pack(pady=(22, 4))
        tk.Label(dlg, text="Dispositivos que el sistema necesita para funcionar.",
                 font=F(10), fg=DIM, bg=BG).pack(pady=(0, 16))
        self._estado_cont = tk.Frame(dlg, bg=BG)
        self._estado_cont.pack(fill="x", padx=30)
        botones = tk.Frame(dlg, bg=BG)
        botones.pack(pady=(24, 0))
        widgets.RoundedButton(botones, "⟳  Volver a detectar", GREEN, "#0c1626",
                              self._redetectar, BG, F(10, True), hover="#54e08f",
                              r=12, padx=16, pady=8).pack(side="left", padx=8)
        widgets.RoundedButton(botones, "Cerrar", BG, TXT, dlg.destroy, BG,
                              F(10, True), hover=BG, r=12, padx=18, pady=8,
                              ancho=self.ANCHO_BOTON, borde=BORDE)\
            .pack(side="left", padx=8)
        self._render_estado_items()

    def _render_estado_items(self):
        cont = self._estado_cont
        for w in cont.winfo_children():
            w.destroy()
        items = [
            (f"Lectora {n}",
             f"{self._sentido_lectora(n).capitalize()} · "
             f"{self._puerto_lectora(n) or 'sin puerto'}",
             self.estado_hw.get(f"lectora{n}"))
            for n in (1, 2)
        ]
        items.append(("Arduino", "Relés + luces del semáforo",
                      self.estado_hw.get("arduino")))
        for nombre, desc, ok in items:
            col = GREEN if ok else RED
            fila = tk.Frame(cont, bg=BG, pady=10)
            fila.pack(fill="x", pady=(0, 4))
            self._crear_punto(fila, "verde" if ok else "rojo", tam=14, bg=BG)\
                .pack(side="left", padx=(4, 12))
            txt = tk.Frame(fila, bg=BG)
            txt.pack(side="left")
            tk.Label(txt, text=nombre, font=F(12, True), fg=TXT, bg=BG).pack(anchor="w")
            tk.Label(txt, text=desc, font=F(9), fg=DIM2, bg=BG).pack(anchor="w")
            tk.Label(fila, text=("CONECTADO" if ok else "NO CONECTADO"),
                     font=F(11, True), fg=col, bg=BG).pack(side="right", padx=4)

    def _redetectar(self):
        if not self.redetectar_cb:
            return

        def worker():
            try:
                estado = self.redetectar_cb()
            except Exception as e:  # noqa: BLE001
                log.error("Error al re-detectar: %s", e)
                return
            self.cola.put(lambda: self._aplicar_estado(estado))

        threading.Thread(target=worker, daemon=True).start()

    # ================= diálogo de ajustes =================
    def _ui_lectora_identificada(self, numero, rut, aj):
        """Una lectora acaba de leer: se resalta su fila y se dice cuál es."""
        sentido = "ENTRADA" if aj.sentido_lectora(numero) == "E" else "SALIDA"
        for n, (marco, sub, pintar, lbl) in getattr(self, "_filas_lectoras", {}).items():
            lbl.config(fg=(GREEN if n == numero else TXT))
        if rut:
            texto = (f"El RUT {rut} fue leído en la LECTORA {numero}, "
                     f"configurada actualmente como {sentido}.")
        else:
            # Lectura ilegible: no se entendió la cédula, pero delató la lectora.
            texto = (f"Lectura recibida en la LECTORA {numero}, configurada "
                     f"actualmente como {sentido}. No se pudo leer el RUT.")
        try:
            self._lbl_ident.config(text=texto, fg=GREEN if rut else YELLOW)
        except Exception:  # noqa: BLE001  el diálogo pudo cerrarse mientras tanto
            pass

    def _refrescar_nombre_terminal(self):
        nombre = None
        if self.controlador is not None:
            try:
                nombre = self.controlador.nombre_terminal()
            except Exception as e:  # noqa: BLE001
                log.error("No se pudo leer el nombre del terminal: %s", e)
        self._ui_nombre_terminal(nombre)

    def _ui_nombre_terminal(self, nombre):
        nombre = (nombre or "").strip()
        if nombre:
            self.lbl_terminal.config(text=f"🖥  {nombre}", fg=DIM)
        else:
            self.lbl_terminal.config(text="🖥  Terminal sin nombre  (⚙ Ajustes)", fg=DIM2)
        if self._dlg is not None and tk.Toplevel.winfo_exists(self._dlg) \
                and getattr(self, "_entrada_nombre", None) is not None:
            # El diálogo está abierto: refleja ahí también el nombre que ganó.
            if self._entrada_nombre.get().strip() != nombre:
                self._entrada_nombre.delete(0, "end")
                self._entrada_nombre.insert(0, nombre)

    def _refrescar_ubicacion(self):
        aj = getattr(self.controlador, "ajustes", None) if self.controlador else None
        ubic = (aj.ubicacion if aj else "") or ""
        if ubic.strip():
            self.lbl_ubicacion.config(text=f"📍  {ubic}", fg=DIM)
        else:
            self.lbl_ubicacion.config(text="📍  Ubicación sin configurar  (⚙ Ajustes)", fg=DIM2)

    # ================= Ajustes (con pestañas) =================
    # Todos los botones del módulo comparten ancho: una fila despareja se lee
    # como si unos botones fueran más importantes que otros.
    ANCHO_BOTON = 132
    # Cada sección se abre por su cuenta desde el pie: la ventana trae solo lo
    # que se pidió, sin pestañas ni navegación interna que recorrer.
    SECCIONES = {
        "terminal": ("Nombre terminal", "Nombre del terminal y ubicación"),
        "ajustes": ("Ajustes", "Lectoras y relés: cuál es entrada y cuál salida"),
        "pruebas": ("Pruebas", "Accionar torniquetes y luces del semáforo"),
    }

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

    # ---- piezas reutilizables ----
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

    # ---- pestaña 1: terminal ----
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

    # ---- pestaña 2: lectoras y relés ----
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

    # ---- pestaña 3: pruebas ----
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
    # ================= reloj =================
    def _tick_reloj(self):
        now = datetime.datetime.now()
        self.lbl_hora.config(text=now.strftime("%H:%M:%S"))
        self.lbl_fecha.config(text=self._fecha(now))
        self.root.after(1000, self._tick_reloj)

    def _hora_hm(self, dt):
        return dt.strftime("%H:%M")

    def _fecha(self, dt):
        return f"{DIAS[dt.weekday()]}, {dt.day} De {MESES[dt.month - 1]} De {dt.year}"

    # ================= badges y luces (imágenes) =================
    def _png_desde_pil(self, img):
        fd, ruta = tempfile.mkstemp(suffix=".png", prefix="ui_")
        os.close(fd)
        img.save(ruta)
        ph = tk.PhotoImage(file=ruta)
        try:
            os.remove(ruta)
        except OSError:
            pass
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

    # ================= errores / crítico =================
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
            pass

    # ================= varios =================
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
