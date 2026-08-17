# -*- coding: utf-8 -*-
"""
Interfaz gráfica (Tkinter) — estilo BAKELITE / CONTROL DE ACCESO.

Reproduce el mockup: barra superior con logo + reloj, mensaje de estado central,
tarjeta "ÚLTIMO REGISTRO", lista de los últimos 5 registros, y pie de página con
un botón de Ajustes (invertir lectoras / relés).

Es thread-safe: los hilos de lectura/validación llaman a los métodos públicos
(mostrar_*), que encolan el cambio; el bucle de Tk lo aplica en el hilo principal.
"""

import math
import queue
import logging
import datetime
import threading
import tkinter as tk

import config

log = logging.getLogger("ui")

DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
         "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]

# ---- Paleta ----
BG = "#0f1c33"
CARD = "#182a45"
CARD_BD = "#2b4066"
ROW_BG = "#14243d"
TXT = "#e8eef7"
DIM = "#8ea3c0"
DIM2 = "#5d7290"
GREEN = "#2fce7c"
BLUE = "#3aa0ff"
RED = "#f0554f"
YELLOW = "#f6c344"

FAM = "DejaVu Sans"

# Tamaño FIJO de la tarjeta: el borde no cambia aunque el nombre sea largo.
CARD_W = 860
CARD_H = 172
NOMBRE_MAX = 24     # el nombre se recorta para no empujar el borde
NOMBRE_HIST_MAX = 26


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
        self.historial = []      # eventos, más reciente primero (máx 5)
        self._dlg = None
        self._dlg_estado = None

        self.root = tk.Tk()
        self.root.title(f"{config.MARCA} — {config.APP_TITULO}")
        self.root.configure(bg=BG)
        self.root.geometry("1440x860")
        self.root.minsize(1120, 720)
        try:
            self.root.attributes("-fullscreen", True)
        except Exception:  # noqa: BLE001
            pass
        self.root.bind("<Escape>", lambda e: self.root.attributes("-fullscreen", False))
        self.root.bind("<F11>", self._toggle_fs)
        self.root.bind("<F2>", lambda e: self._abrir_ajustes())

        self._construir()
        self._bind_sim()

        self._tick_reloj()
        self._render_historial()
        self._aplicar_estado(self.estado_hw)
        self.root.after(50, self._drenar_cola)
        self.mostrar_esperando()

        # Si al arrancar falta hardware (y no es simulación), muestra la pantalla de estado.
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

        if self.sim:
            tk.Label(
                r,
                text="SIM  ·  1–6 entrada   ·   Ctrl+1–6 salida   ·   0 no registrado"
                     "   ·   .  error de lectura   ·   -  sin conexión",
                font=F(9), fg=DIM2, bg=BG,
            ).grid(row=4, column=0, pady=(0, 8))

    def _barra_superior(self):
        top = tk.Frame(self.root, bg=BG)
        top.grid(row=0, column=0, sticky="ew", padx=44, pady=(26, 0))
        top.grid_columnconfigure(0, weight=1)

        left = tk.Frame(top, bg=BG)
        left.grid(row=0, column=0, sticky="w")
        self._logo(left)
        textos = tk.Frame(left, bg=BG)
        textos.pack(side="left")
        linea1 = tk.Frame(textos, bg=BG)
        linea1.pack(anchor="w")
        tk.Label(linea1, text=config.MARCA, font=F(20, True), fg=TXT, bg=BG).pack(side="left")
        tk.Label(linea1, text="   |   ", font=F(16), fg=DIM2, bg=BG).pack(side="left")
        tk.Label(linea1, text=config.APP_TITULO, font=F(13), fg=DIM, bg=BG).pack(side="left")
        tk.Label(textos, text=config.SUBTITULO, font=F(11), fg=YELLOW, bg=BG)\
            .pack(anchor="w", pady=(2, 0))

        right = tk.Frame(top, bg=BG)
        right.grid(row=0, column=1, sticky="e")
        self.lbl_hora = tk.Label(right, text="", font=F(38, True), fg=TXT, bg=BG)
        self.lbl_hora.pack(anchor="e")
        self.lbl_fecha = tk.Label(right, text="", font=F(12), fg=DIM, bg=BG)
        self.lbl_fecha.pack(anchor="e")

    def _logo(self, parent):
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
        cont.grid(row=1, column=0, pady=(30, 0))

        b = tk.Frame(cont, bg=BG)
        b.pack()
        self.dot = tk.Canvas(b, width=16, height=16, bg=BG, highlightthickness=0)
        self.dot_id = self.dot.create_oval(3, 3, 13, 13, fill=GREEN, outline="")
        self.dot.pack(side="left", padx=(0, 12))
        self.lbl_estado = tk.Label(b, text="", font=F(19, True), fg=GREEN, bg=BG)
        self.lbl_estado.pack(side="left")

        # Aviso de hardware faltante (oculto cuando todo está conectado).
        self.lbl_aviso = tk.Label(cont, text="", font=F(11, True), fg=YELLOW, bg=BG,
                                  cursor="hand2")
        self.lbl_aviso.pack(pady=(8, 0))
        self.lbl_aviso.bind("<Button-1>", lambda e: self._abrir_estado())

    def _centro(self):
        centro = tk.Frame(self.root, bg=BG)
        centro.grid(row=2, column=0)

        self._tarjeta(centro)
        self._panel_historial(centro)

    def _tarjeta(self, parent):
        # Tamaño fijo + sin propagación: el borde NUNCA cambia con el nombre.
        card = tk.Frame(parent, bg=CARD, highlightbackground=CARD_BD, highlightthickness=1,
                        width=CARD_W, height=CARD_H)
        card.pack(pady=(18, 10))
        card.grid_propagate(False)
        card.grid_rowconfigure(0, weight=1)
        card.grid_columnconfigure(0, weight=1)

        # --- info (sin foto) ---
        info = tk.Frame(card, bg=CARD)
        info.grid(row=0, column=0, sticky="w", padx=(30, 10))
        tk.Label(info, text="ÚLTIMO REGISTRO", font=F(10, True), fg=DIM2, bg=CARD).pack(anchor="w")
        self.lbl_nombre = tk.Label(info, text="—", font=F(28, True), fg=TXT, bg=CARD,
                                   anchor="w")
        self.lbl_nombre.pack(anchor="w", pady=(2, 4))
        self.lbl_cedula = tk.Label(info, text="Cédula: —", font=F(14), fg=DIM, bg=CARD)
        self.lbl_cedula.pack(anchor="w")

        fila = tk.Frame(info, bg=CARD)
        fila.pack(anchor="w", pady=(12, 0))
        self.lbl_hora_reg = tk.Label(fila, text="—", font=F(12), fg=DIM, bg=CARD)
        self.lbl_hora_reg.pack(side="left", padx=(0, 14))
        self.lbl_tag = tk.Label(fila, text="ENTRADA", font=F(10, True),
                                fg="#0c1626", bg=GREEN, padx=12, pady=3)
        self.lbl_tag.pack(side="left")

        # --- veredicto (círculo suavizado) ---
        estado = tk.Frame(card, bg=CARD)
        estado.grid(row=0, column=1, sticky="e", padx=(10, 34))
        self._preparar_badges()
        if self._badge_ok:
            self.badge = tk.Label(estado, image="", bg=CARD, bd=0)
            self.badge.pack()
        else:
            # Respaldo sin Pillow: glifo "●" (lo suaviza el motor de fuentes).
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

    def _preparar_badges(self):
        """Genera los círculos (AUTORIZADO/DENEGADO/SIN RED) con antialiasing usando
        Pillow (supersampling + LANCZOS) y los carga como PNG con tk.PhotoImage
        (Tk 8.6 lee PNG nativamente, sin necesidad de ImageTk).
        Si Pillow no está, usa el respaldo de glifo."""
        self._badge_img = {}
        self._badge_ok = False
        try:
            from PIL import Image, ImageDraw
        except Exception:  # noqa: BLE001
            return

        import os
        import tempfile

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
            else:  # warn "!"
                dr.line([(0.5 * D, 0.28 * D), (0.5 * D, 0.56 * D)], fill=dark, width=w)
                punto(dr, (0.5 * D, 0.70 * D), w * 1.1)
            img = img.resize((size, size), Image.LANCZOS)
            fd, ruta = tempfile.mkstemp(suffix=".png", prefix=f"badge_{tipo}_")
            os.close(fd)
            img.save(ruta)
            foto = tk.PhotoImage(file=ruta)   # Tk 8.6 lee PNG
            try:
                os.remove(ruta)
            except OSError:
                pass
            return foto

        try:
            self._badge_img = {
                "ok": dibujar(GREEN, "ok"),
                "no": dibujar(RED, "no"),
                "warn": dibujar(YELLOW, "warn"),
            }
            self._badge_ok = True
        except Exception as e:  # noqa: BLE001
            log.error("No se pudieron generar los badges Pillow: %s", e)
            self._badge_ok = False

    def _panel_historial(self, parent):
        panel = tk.Frame(parent, bg=CARD, highlightbackground=CARD_BD, highlightthickness=1)
        panel.pack(pady=(4, 6), fill="x", ipadx=18, ipady=12)
        tk.Label(panel, text="ÚLTIMOS REGISTROS", font=F(10, True), fg=DIM2, bg=CARD)\
            .pack(anchor="w", padx=10, pady=(2, 8))
        self.hist_cont = tk.Frame(panel, bg=CARD)
        self.hist_cont.pack(fill="x", padx=6)
        self.hist_cont.grid_columnconfigure(1, weight=1)

    def _pie(self):
        foot = tk.Frame(self.root, bg=BG)
        foot.grid(row=3, column=0, sticky="ew", padx=44, pady=(0, 18))
        foot.grid_columnconfigure(0, weight=1)
        tk.Label(foot, text=config.TERMINAL_NOMBRE, font=F(10), fg=DIM2, bg=BG)\
            .grid(row=0, column=0, sticky="w")

        der = tk.Frame(foot, bg=BG)
        der.grid(row=0, column=1, sticky="e")
        tk.Button(der, text="🔌  Estado", font=F(10, True),
                  fg=DIM, bg=CARD, activebackground=CARD_BD, activeforeground=TXT,
                  relief="flat", bd=0, padx=12, pady=4, cursor="hand2",
                  command=self._abrir_estado).pack(side="left", padx=(0, 8))
        tk.Button(der, text="⚙  Ajustes", font=F(10, True),
                  fg=DIM, bg=CARD, activebackground=CARD_BD, activeforeground=TXT,
                  relief="flat", bd=0, padx=12, pady=4, cursor="hand2",
                  command=self._abrir_ajustes).pack(side="left", padx=(0, 16))
        self.lbl_conexion = tk.Label(der, text="Sistema en línea", font=F(10), fg=DIM2, bg=BG)
        self.lbl_conexion.pack(side="left")

    # ================= API pública (thread-safe) =================
    def mostrar_esperando(self):
        self.cola.put(self._ui_esperando)

    def mostrar_consultando(self, sentido):
        self.cola.put(lambda: self._ui_consultando(sentido))

    def mostrar_resultado(self, resultado):
        self.cola.put(lambda: self._ui_resultado(resultado))

    def set_conexion(self, texto, ok=True):
        self.cola.put(lambda: self.lbl_conexion.config(
            text=texto, fg=DIM2 if ok else YELLOW))

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
        self.dot.itemconfig(self.dot_id, fill=color)
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
        else:  # 0
            self._set_banner(RED, "ACCESO NO HABILITADO")
            self._veredicto("no", "DENEGADO", RED)
            self._actualizar_registro(r)

        if codigo in (0, 1):
            self._push_historial(r)

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
        self.lbl_hora_reg.config(text=self._hora_12(datetime.datetime.now()))
        if r.sentido == "E":
            self.lbl_tag.config(text="ENTRADA", bg=GREEN)
        else:
            self.lbl_tag.config(text="SALIDA", bg=BLUE)

    # ================= historial (últimos 5) =================
    def _push_historial(self, r):
        self.historial.insert(0, {
            "hora": self._hora_12(datetime.datetime.now()),
            "nombre": r.nombre or "Desconocido",
            "rut": r.rut_display or "—",
            "sentido": r.sentido,
            "autorizado": bool(r.autorizado),
        })
        del self.historial[5:]
        self._render_historial()

    def _render_historial(self):
        for w in self.hist_cont.winfo_children():
            w.destroy()

        if not self.historial:
            tk.Label(self.hist_cont, text="Sin registros aún.",
                     font=F(11), fg=DIM2, bg=CARD).grid(row=0, column=0, sticky="w",
                                                        padx=8, pady=6)
            return

        for i, ev in enumerate(self.historial):
            color = GREEN if ev["autorizado"] else RED
            fila = tk.Frame(self.hist_cont, bg=ROW_BG)
            fila.grid(row=i, column=0, columnspan=5, sticky="ew", pady=2)
            fila.grid_columnconfigure(1, weight=1)

            tk.Label(fila, text=ev["hora"], font=F(11), fg=DIM, bg=ROW_BG, width=11,
                     anchor="w").grid(row=0, column=0, sticky="w", padx=(10, 8), pady=6)
            tk.Label(fila, text=recorta(ev["nombre"], NOMBRE_HIST_MAX), font=F(12, True),
                     fg=TXT, bg=ROW_BG, anchor="w", width=NOMBRE_HIST_MAX)\
                .grid(row=0, column=1, sticky="w", padx=(0, 8))
            tk.Label(fila, text=ev["rut"], font=F(11), fg=DIM, bg=ROW_BG,
                     anchor="w", width=14).grid(row=0, column=2, sticky="w", padx=(0, 10))
            tag_txt = "ENTRADA" if ev["sentido"] == "E" else "SALIDA"
            tag_col = GREEN if ev["sentido"] == "E" else BLUE
            tk.Label(fila, text=tag_txt, font=F(9, True), fg=tag_col, bg=ROW_BG,
                     width=8).grid(row=0, column=3, padx=(0, 12))
            tk.Label(fila, text=("✓ AUTORIZADO" if ev["autorizado"] else "✕ DENEGADO"),
                     font=F(10, True), fg=color, bg=ROW_BG, width=13, anchor="e")\
                .grid(row=0, column=4, sticky="e", padx=(0, 12))

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
            if not self.sim:
                self.lbl_conexion.config(text="Hardware incompleto", fg=YELLOW)
        else:
            self.lbl_aviso.config(text="")
            if not self.sim:
                self.lbl_conexion.config(text="Sistema en línea", fg=DIM2)

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

        tk.Label(dlg, text="ESTADO DE CONEXIÓN", font=F(15, True), fg=TXT, bg=BG)\
            .pack(pady=(22, 4))
        tk.Label(dlg, text="Dispositivos que el sistema necesita para funcionar.",
                 font=F(10), fg=DIM, bg=BG).pack(pady=(0, 16))

        self._estado_cont = tk.Frame(dlg, bg=BG)
        self._estado_cont.pack(fill="x", padx=30)

        botones = tk.Frame(dlg, bg=BG)
        botones.pack(pady=(24, 0))
        tk.Button(botones, text="⟳  Volver a detectar", font=F(10, True),
                  fg="#0c1626", bg=GREEN, relief="flat", bd=0, padx=16, pady=7,
                  cursor="hand2", command=self._redetectar).pack(side="left", padx=8)
        tk.Button(botones, text="Cerrar", font=F(10, True), fg=TXT, bg=CARD_BD,
                  relief="flat", bd=0, padx=18, pady=7, cursor="hand2",
                  command=dlg.destroy).pack(side="left", padx=8)

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
            fila = tk.Frame(cont, bg=CARD)
            fila.pack(fill="x", pady=5, ipady=10)
            tk.Label(fila, text="●", font=F(15), fg=col, bg=CARD).pack(side="left", padx=(14, 10))
            txt = tk.Frame(fila, bg=CARD)
            txt.pack(side="left")
            tk.Label(txt, text=nombre, font=F(12, True), fg=TXT, bg=CARD).pack(anchor="w")
            tk.Label(txt, text=desc, font=F(9), fg=DIM2, bg=CARD).pack(anchor="w")
            tk.Label(fila, text=("CONECTADO" if ok else "NO CONECTADO"), font=F(11, True),
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
    def _abrir_ajustes(self):
        aj = getattr(self.controlador, "ajustes", None) if self.controlador else None
        if aj is None:
            return
        if self._dlg is not None and tk.Toplevel.winfo_exists(self._dlg):
            self._dlg.lift()
            return

        dlg = tk.Toplevel(self.root, bg=BG)
        self._dlg = dlg
        dlg.title("Ajustes de hardware")
        dlg.configure(bg=BG)
        dlg.geometry("620x580")
        dlg.transient(self.root)
        dlg.resizable(False, False)

        tk.Label(dlg, text="AJUSTES DE HARDWARE", font=F(15, True), fg=TXT, bg=BG)\
            .pack(pady=(22, 4))
        tk.Label(dlg, text="Corrige el sentido si las lectoras o los relés quedaron al revés.",
                 font=F(10), fg=DIM, bg=BG).pack(pady=(0, 14))

        prev = tk.Label(dlg, text="", font=("DejaVu Sans Mono", 11), fg=TXT, bg=CARD,
                        justify="left", padx=18, pady=12)
        prev.pack(padx=26, fill="x")

        def actualizar_preview():
            l1 = "SALIDA" if aj.invertir_lectoras else "ENTRADA"
            l2 = "ENTRADA" if aj.invertir_lectoras else "SALIDA"
            re = aj.comando_rele("E")
            rs = aj.comando_rele("S")
            prev.config(text=(f"Lectora 1   →   {l1}\n"
                              f"Lectora 2   →   {l2}\n"
                              f"Relé ENTRADA →   {re}\n"
                              f"Relé SALIDA  →   {rs}"))

        def toggle_row(texto, getter, setter):
            fr = tk.Frame(dlg, bg=BG)
            fr.pack(fill="x", padx=30, pady=(14, 0))
            tk.Label(fr, text=texto, font=F(11), fg=TXT, bg=BG).pack(side="left")
            btn = tk.Button(fr, width=11, font=F(10, True), relief="flat", bd=0,
                            cursor="hand2")

            def refresh():
                on = getter()
                btn.config(text="INVERTIDO" if on else "NORMAL",
                           bg=(YELLOW if on else CARD_BD),
                           fg=("#0c1626" if on else TXT),
                           activebackground=(YELLOW if on else CARD_BD))

            def click():
                setter(not getter())
                aj.guardar()
                refresh()
                actualizar_preview()

            btn.config(command=click)
            btn.pack(side="right")
            refresh()

        toggle_row("Invertir lectoras  (Entrada ↔ Salida)",
                   lambda: aj.invertir_lectoras,
                   lambda v: setattr(aj, "invertir_lectoras", v))
        toggle_row("Invertir relés  (Entrada ↔ Salida)",
                   lambda: aj.invertir_reles,
                   lambda v: setattr(aj, "invertir_reles", v))

        # --- probar relés ---
        tk.Label(dlg, text="Probar torniquetes (dispara el relé para ver cuál abre):",
                 font=F(10), fg=DIM, bg=BG).pack(pady=(20, 6))
        pr = tk.Frame(dlg, bg=BG)
        pr.pack()
        tk.Button(pr, text="▶  Probar ENTRADA", font=F(10, True), fg="#0c1626", bg=GREEN,
                  relief="flat", bd=0, padx=14, pady=6, cursor="hand2",
                  command=lambda: self.controlador.probar_rele("E")).pack(side="left", padx=8)
        tk.Button(pr, text="▶  Probar SALIDA", font=F(10, True), fg="#0c1626", bg=BLUE,
                  relief="flat", bd=0, padx=14, pady=6, cursor="hand2",
                  command=lambda: self.controlador.probar_rele("S")).pack(side="left", padx=8)

        # --- probar luces ---
        tk.Label(dlg, text="Probar luces del semáforo:",
                 font=F(10), fg=DIM, bg=BG).pack(pady=(18, 6))
        pl = tk.Frame(dlg, bg=BG)
        pl.pack()
        for texto, color, bg_btn, fg_btn in (
            ("Azul", "azul", BLUE, "#0c1626"),
            ("Verde", "verde", GREEN, "#0c1626"),
            ("Rojo", "rojo", RED, "#0c1626"),
            ("Amarillo", "amarillo", YELLOW, "#0c1626"),
            ("Apagar", "off", CARD_BD, TXT),
        ):
            tk.Button(pl, text=texto, font=F(10, True), fg=fg_btn, bg=bg_btn,
                      relief="flat", bd=0, padx=12, pady=6, cursor="hand2",
                      command=lambda c=color: self.controlador.probar_luz(c))\
                .pack(side="left", padx=5)

        tk.Button(dlg, text="Cerrar", font=F(10, True), fg=TXT, bg=CARD_BD,
                  relief="flat", bd=0, padx=18, pady=6, cursor="hand2",
                  command=dlg.destroy).pack(pady=(22, 0))

        actualizar_preview()

    # ================= reloj =================
    def _tick_reloj(self):
        now = datetime.datetime.now()
        self.lbl_hora.config(text=self._hora_12s(now))
        self.lbl_fecha.config(text=self._fecha(now))
        self.root.after(1000, self._tick_reloj)

    @staticmethod
    def _sufijo(dt):
        return "a. m." if dt.hour < 12 else "p. m."

    def _hora_12s(self, dt):
        return f"{dt.strftime('%I:%M:%S')} {self._sufijo(dt)}"

    def _hora_12(self, dt):
        return f"{dt.strftime('%I:%M')} {self._sufijo(dt)}"

    def _fecha(self, dt):
        return f"{DIAS[dt.weekday()]}, {dt.day} De {MESES[dt.month - 1]} De {dt.year}"

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
