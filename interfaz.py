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

import config
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

CARD_W, CARD_H = 860, 168
HIST_W, HIST_H = 860, 300
NOMBRE_MAX = 24
NOMBRE_HIST_MAX = 22
MOTIVO_HIST_MAX = 26        # motivo del rechazo en la tabla acumulada
HIST_FILAS = 5              # filas fijas de la tabla (no se recrean)
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

        self.root = tk.Tk()
        self.root.title(f"{config.MARCA} — {config.APP_TITULO}")
        self.root.configure(bg=BG)
        self.root.geometry("1440x860")
        self.root.minsize(1200, 760)
        self.root.report_callback_exception = self._on_tk_error
        try:
            self.root.attributes("-fullscreen", True)
        except Exception:  # noqa: BLE001
            pass
        self.root.bind("<Escape>", lambda e: self.root.attributes("-fullscreen", False))
        self.root.bind("<F11>", self._toggle_fs)
        self.root.bind("<F2>", lambda e: self._abrir_ajustes())

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

        if not self.sim and not all(self.estado_hw.values()):
            self.root.after(400, self._abrir_estado)

    # ================= construcción =================
    def _construir(self):
        r = self.root
        r.grid_rowconfigure(2, weight=1)
        r.grid_columnconfigure(0, weight=1)
        self._barra_superior()
        self._banner()
        self._centro()
        self._pie()
        self._powered_by()
        if self.sim:
            tk.Label(
                r,
                text="SIM  ·  1–6 entrada   ·   Ctrl+1–6 salida   ·   0 no registrado"
                     "   ·   .  error de lectura   ·   -  sin conexión",
                font=F(9), fg=DIM2, bg=BG,
            ).grid(row=4, column=0, pady=(0, 8))

    def _barra_superior(self):
        top = tk.Frame(self.root, bg=BG)
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
        cont = tk.Frame(self.root, bg=BG)
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
        centro = tk.Frame(self.root, bg=BG)
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
        barra = tk.Frame(self.root, bg=BG)
        barra.grid(row=5, column=0, pady=(2, 14))
        tk.Label(barra, text="Powered by", font=F(9), fg=DIM2, bg=BG)\
            .pack(side="left", padx=(0, 9))
        if self._logo_sopytec is not None:
            tk.Label(barra, image=self._logo_sopytec, bg=BG, bd=0).pack(side="left")
        else:
            tk.Label(barra, text="sopytec", font=F(12, True), fg=TXT, bg=BG).pack(side="left")

    def _pie(self):
        foot = tk.Frame(self.root, bg=BG)
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
        widgets.RoundedButton(der, "🔌  Estado", CARD, DIM, self._abrir_estado, BG,
                              F(10, True), hover=CARD_BD, r=10, padx=12, pady=5)\
            .pack(side="left", padx=(0, 8))
        widgets.RoundedButton(der, "⚙  Ajustes", CARD, DIM, self._abrir_ajustes, BG,
                              F(10, True), hover=CARD_BD, r=10, padx=12, pady=5)\
            .pack(side="left", padx=(0, 16))
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

    def _ui_consultando(self, sentido):
        self._set_banner(BLUE, "VALIDANDO ACCESO…")

    def _ui_resultado(self, r):
        codigo = r.codigo
        if codigo == 1:
            self._set_banner(GREEN, "ACCESO AUTORIZADO")
            self._veredicto("ok", "AUTORIZADO", GREEN)
            self._actualizar_registro(r)
        elif codigo == 4:
            self._set_banner(YELLOW, "SIN CONEXIÓN A RED")
            self._veredicto("warn", "SIN RED", YELLOW)
        elif codigo in (2, 3):
            self._set_banner(RED, "ERROR DE LECTURA — REINTENTE")
            self._veredicto("no", "ERROR", RED)
        else:
            self._set_banner(RED, "ACCESO NO HABILITADO")
            self._veredicto("no", "DENEGADO", RED)
            self._actualizar_registro(r)
        if codigo in (0, 1):
            self._push_historial(r)

    def _ui_luz(self, color):
        c, etq, _sig = LUCES.get(color, LUCES["off"])
        if self._luz_ok:
            self.luz_big.config(image=self._luz_img[color])
        self.luz_cap.config(text=etq, fg=c)

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
    def _aplicar_estado(self, estado):
        self.estado_hw = dict(estado)
        faltan = []
        if not estado.get("lectora1"):
            faltan.append("Lectora 1 (entrada)")
        if not estado.get("lectora2"):
            faltan.append("Lectora 2 (salida)")
        if not estado.get("arduino"):
            faltan.append("Arduino (relés/luces)")
        if faltan:
            self.lbl_aviso.config(
                text="⚠  Falta conectar:  " + "   ·   ".join(faltan) + "     (toca para ver)")
        else:
            self.lbl_aviso.config(text="")
        if self._dlg_estado is not None and tk.Toplevel.winfo_exists(self._dlg_estado):
            self._render_estado_items()

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
        widgets.RoundedButton(botones, "Cerrar", CARD_BD, TXT, dlg.destroy, BG,
                              F(10, True), hover="#38507a",
                              r=12, padx=18, pady=8).pack(side="left", padx=8)
        self._render_estado_items()

    def _render_estado_items(self):
        cont = self._estado_cont
        for w in cont.winfo_children():
            w.destroy()
        items = [
            ("Lectora 1", "Entrada · lee la cédula", self.estado_hw.get("lectora1")),
            ("Lectora 2", "Salida · lee la cédula", self.estado_hw.get("lectora2")),
            ("Arduino", "Relés + luces del semáforo", self.estado_hw.get("arduino")),
        ]
        for nombre, desc, ok in items:
            col = GREEN if ok else RED
            fila = widgets.RoundedPanel(cont, 500, 56, 12, CARD, BG, pad=0)
            fila.pack(fill="x", pady=5)
            inner = fila.inner
            self._crear_punto(inner, "verde" if ok else "rojo", tam=14, bg=CARD)\
                .pack(side="left", padx=(14, 10))
            txt = tk.Frame(inner, bg=CARD)
            txt.pack(side="left")
            tk.Label(txt, text=nombre, font=F(12, True), fg=TXT, bg=CARD).pack(anchor="w")
            tk.Label(txt, text=desc, font=F(9), fg=DIM2, bg=CARD).pack(anchor="w")
            tk.Label(inner, text=("CONECTADO" if ok else "NO CONECTADO"), font=F(11, True),
                     fg=col, bg=CARD).pack(side="right", padx=16)

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

    def _abrir_ajustes(self):
        aj = getattr(self.controlador, "ajustes", None) if self.controlador else None
        if aj is None:
            return
        if self._dlg is not None and tk.Toplevel.winfo_exists(self._dlg):
            self._dlg.lift()
            return
        dlg = tk.Toplevel(self.root, bg=BG)
        self._dlg = dlg
        dlg.title("Ajustes")
        dlg.configure(bg=BG)
        dlg.geometry("640x760")
        dlg.transient(self.root)
        dlg.resizable(False, False)

        tk.Label(dlg, text="AJUSTES", font=F(15, True), fg=TXT, bg=BG).pack(pady=(20, 2))

        # --- Nombre del terminal (se sincroniza con Bakelite) ---
        tk.Label(dlg, text="Nombre del terminal:", font=F(10, True), fg=DIM, bg=BG)\
            .pack(anchor="w", padx=30, pady=(12, 2))
        fn = tk.Frame(dlg, bg=BG)
        fn.pack(fill="x", padx=30)
        entrada_nom = tk.Entry(fn, font=F(12), bg=CARD, fg=TXT, insertbackground=TXT,
                               relief="flat", bd=8)
        entrada_nom.pack(side="left", fill="x", expand=True, ipady=2)
        entrada_nom.insert(0, (self.controlador.nombre_terminal() or "")
                           if self.controlador else "")
        self._entrada_nombre = entrada_nom
        aviso_nom = tk.Label(dlg, text="Se sincroniza con Bakelite: gana el último cambio.",
                             font=F(9), fg=DIM2, bg=BG)
        aviso_nom.pack(anchor="w", padx=30, pady=(4, 0))

        def guardar_nombre():
            nuevo = entrada_nom.get().strip()
            if not nuevo:
                aviso_nom.config(text="El nombre no puede quedar vacío.", fg=RED)
                return
            if self.controlador is None:
                return
            quedo = self.controlador.renombrar_terminal(nuevo, usuario="operador")
            if quedo is None:
                aviso_nom.config(text="No se pudo guardar (BD local no disponible).",
                                 fg=RED)
                return
            self._ui_nombre_terminal(quedo)
            aviso_nom.config(text="Guardado. Se sube a Bakelite en cuanto haya conexión.",
                             fg=DIM2)

        widgets.RoundedButton(fn, "Guardar", GREEN, "#0c1626", guardar_nombre, BG,
                              F(10, True), hover="#54e08f", r=10, padx=14, pady=7)\
            .pack(side="left", padx=(8, 0))

        # --- Ubicación del torniquete ---
        tk.Label(dlg, text="Ubicación del torniquete:", font=F(10, True), fg=DIM, bg=BG)\
            .pack(anchor="w", padx=30, pady=(12, 2))
        fu = tk.Frame(dlg, bg=BG)
        fu.pack(fill="x", padx=30)
        entrada = tk.Entry(fu, font=F(12), bg=CARD, fg=TXT, insertbackground=TXT,
                           relief="flat", bd=8)
        entrada.pack(side="left", fill="x", expand=True, ipady=2)
        entrada.insert(0, aj.ubicacion or "")

        def guardar_ubic():
            aj.ubicacion = entrada.get().strip()
            aj.guardar()
            self._refrescar_ubicacion()

        widgets.RoundedButton(fu, "Guardar", GREEN, "#0c1626", guardar_ubic, BG,
                              F(10, True), hover="#54e08f", r=10, padx=14, pady=7)\
            .pack(side="left", padx=(8, 0))

        # --- Inversión + preview ---
        prev = tk.Label(dlg, text="", font=("DejaVu Sans Mono", 11), fg=TXT, bg=CARD,
                        justify="left", padx=18, pady=12)
        prev.pack(padx=30, fill="x", pady=(16, 0))

        def actualizar_preview():
            l1 = "SALIDA" if aj.invertir_lectoras else "ENTRADA"
            l2 = "ENTRADA" if aj.invertir_lectoras else "SALIDA"
            prev.config(text=(f"Lectora 1   →   {l1}\n"
                              f"Lectora 2   →   {l2}\n"
                              f"Relé ENTRADA →   {aj.comando_rele('E')}\n"
                              f"Relé SALIDA  →   {aj.comando_rele('S')}"))

        def toggle_row(texto, getter, setter):
            fr = tk.Frame(dlg, bg=BG)
            fr.pack(fill="x", padx=30, pady=(12, 0))
            tk.Label(fr, text=texto, font=F(11), fg=TXT, bg=BG).pack(side="left")
            btn = widgets.RoundedButton(fr, "NORMAL", CARD_BD, TXT, None, BG,
                                        F(10, True), hover=CARD_BD, r=10, padx=14, pady=6)

            def refresh():
                on = getter()
                btn.set_style(text=("INVERTIDO" if on else "NORMAL"),
                              fill=(YELLOW if on else CARD_BD),
                              fg=("#0c1626" if on else TXT),
                              hover=(YELLOW if on else CARD_BD))

            def click():
                setter(not getter())
                aj.guardar()
                refresh()
                actualizar_preview()

            btn._command = click
            btn.pack(side="right")
            refresh()

        toggle_row("Invertir lectoras  (Entrada ↔ Salida)",
                   lambda: aj.invertir_lectoras,
                   lambda v: setattr(aj, "invertir_lectoras", v))
        toggle_row("Invertir relés  (Entrada ↔ Salida)",
                   lambda: aj.invertir_reles,
                   lambda v: setattr(aj, "invertir_reles", v))

        # --- probar relés ---
        tk.Label(dlg, text="Probar torniquetes:", font=F(10), fg=DIM, bg=BG).pack(pady=(18, 6))
        pr = tk.Frame(dlg, bg=BG)
        pr.pack()
        widgets.RoundedButton(pr, "▶  ENTRADA", GREEN, "#0c1626",
                              lambda: self.controlador.probar_rele("E"), BG, F(10, True),
                              hover="#54e08f", r=10, padx=14, pady=7).pack(side="left", padx=8)
        widgets.RoundedButton(pr, "▶  SALIDA", BLUE, "#0c1626",
                              lambda: self.controlador.probar_rele("S"), BG, F(10, True),
                              hover="#63b6ff", r=10, padx=14, pady=7).pack(side="left", padx=8)

        # --- probar luces ---
        tk.Label(dlg, text="Probar luces del semáforo:", font=F(10), fg=DIM, bg=BG).pack(pady=(16, 6))
        pl = tk.Frame(dlg, bg=BG)
        pl.pack()
        for texto, color, bg_btn, hov in (
            ("Azul", "azul", BLUE, "#63b6ff"), ("Verde", "verde", GREEN, "#54e08f"),
            ("Rojo", "rojo", RED, "#f47b76"), ("Amarillo", "amarillo", YELLOW, "#f9d074"),
            ("Apagar", "off", CARD_BD, "#38507a"),
        ):
            fg_btn = TXT if color == "off" else "#0c1626"
            widgets.RoundedButton(pl, texto, bg_btn, fg_btn,
                                  lambda c=color: self.controlador.probar_luz(c), BG,
                                  F(10, True), hover=hov, r=10, padx=11, pady=6)\
                .pack(side="left", padx=4)

        widgets.RoundedButton(dlg, "Cerrar", CARD_BD, TXT, dlg.destroy, BG, F(10, True),
                              hover="#38507a", r=12, padx=18, pady=8).pack(pady=(20, 0))
        actualizar_preview()

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
        self._luz_img = {}
        self._luz_ok = False
        try:
            from PIL import Image, ImageDraw
        except Exception:  # noqa: BLE001
            return
        S, size = 4, 150
        D = size * S

        def rgb(hx, a=255):
            hx = hx.lstrip("#")
            return tuple(int(hx[i:i + 2], 16) for i in (0, 2, 4)) + (a,)

        def luz(color_hex):
            img = Image.new("RGBA", (D, D), (0, 0, 0, 0))
            dr = ImageDraw.Draw(img)
            dr.ellipse([0, 0, D - 1, D - 1], fill=rgb(color_hex, 45))        # halo tenue
            m = int(D * 0.15)
            dr.ellipse([m, m, D - m, D - m], fill=rgb(color_hex, 255))       # núcleo sólido
            return self._png_desde_pil(img.resize((size, size), Image.LANCZOS))

        try:
            for nombre, (c, _e, _s) in LUCES.items():
                self._luz_img[nombre] = luz(c)
            self._luz_ok = True
        except Exception as e:  # noqa: BLE001
            log.error("No se pudieron generar las luces: %s", e)

    # ================= errores / crítico =================
    def _mostrar_critico(self, texto):
        self.lbl_critico.config(text="  ⚠  ERROR CRÍTICO: " + str(texto)[:120] + "  ")
        self.lbl_critico.pack(in_=self._banner_cont, before=self._banner_b, pady=(0, 8))

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
