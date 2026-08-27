# -*- coding: utf-8 -*-
"""
Widgets con esquinas redondeadas (píldoras, botones y paneles), dibujados con
Pillow + supersampling para que se vean suaves. Se cargan como PNG con
tk.PhotoImage (Tk 8.6 lee PNG; no necesita ImageTk).

Si Pillow no está disponible, cada helper cae a un equivalente cuadrado de Tk,
para que la app siga funcionando.
"""

import os
import tempfile
import logging
import tkinter as tk
import tkinter.font as tkfont

log = logging.getLogger("widgets")

try:
    from PIL import Image, ImageDraw
    _PIL = True
except Exception:  # noqa: BLE001
    _PIL = False

_CACHE = {}   # (w,h,r,fill) -> tk.PhotoImage (mantiene las referencias vivas)


def _rgb(hx):
    hx = hx.lstrip("#")
    return tuple(int(hx[i:i + 2], 16) for i in (0, 2, 4)) + (255,)


def _pil_to_photo(img):
    fd, ruta = tempfile.mkstemp(suffix=".png", prefix="w_")
    os.close(fd)
    img.save(ruta)
    ph = tk.PhotoImage(file=ruta)
    try:
        os.remove(ruta)
    except OSError:
        pass
    return ph


def _dibuja_redondo(dr, box, r, fill):
    x1, y1, x2, y2 = box
    r = min(r, (x2 - x1) // 2, (y2 - y1) // 2)
    dr.rectangle([x1 + r, y1, x2 - r, y2], fill=fill)
    dr.rectangle([x1, y1 + r, x2, y2 - r], fill=fill)
    dr.pieslice([x1, y1, x1 + 2 * r, y1 + 2 * r], 180, 270, fill=fill)
    dr.pieslice([x2 - 2 * r, y1, x2, y1 + 2 * r], 270, 360, fill=fill)
    dr.pieslice([x1, y2 - 2 * r, x1 + 2 * r, y2], 90, 180, fill=fill)
    dr.pieslice([x2 - 2 * r, y2 - 2 * r, x2, y2], 0, 90, fill=fill)


def rounded_image(w, h, r, fill, borde=None, grosor=2):
    """tk.PhotoImage de un rectángulo redondeado (transparente fuera del borde).

    Con `borde` queda solo el contorno: se dibuja la figura del color del borde
    y encima otra más chica del color de fondo. Sirve para botones que no deben
    aparecer como un bloque de color sobre el panel.

    Nunca lanza: si algo falla, devuelve None y el llamador cae a un estilo plano."""
    if not _PIL:
        return None
    clave = (w, h, r, fill, borde, grosor)
    if clave in _CACHE:
        return _CACHE[clave]
    try:
        S = 4
        img = Image.new("RGBA", (w * S, h * S), (0, 0, 0, 0))
        dr = ImageDraw.Draw(img)
        if borde:
            _dibuja_redondo(dr, [0, 0, w * S - 1, h * S - 1], r * S, _rgb(borde))
            g = grosor * S
            _dibuja_redondo(dr, [g, g, w * S - 1 - g, h * S - 1 - g],
                            max(r * S - g, 1), _rgb(fill))
        else:
            _dibuja_redondo(dr, [0, 0, w * S - 1, h * S - 1], r * S, _rgb(fill))
        ph = _pil_to_photo(img.resize((w, h), Image.LANCZOS))
        _CACHE[clave] = ph
        return ph
    except Exception as e:  # noqa: BLE001
        log.error("rounded_image(%s) falló: %s", clave, e)
        _CACHE[clave] = None
        return None


def make_pill(parent, text, fill, fg, bg, font, padx=16, pady=6, r=None):
    """Etiqueta tipo píldora (fondo redondeado ajustado al texto)."""
    f = tkfont.Font(font=font)
    w = f.measure(text) + padx * 2
    h = f.metrics("linespace") + pady * 2
    if r is None:
        r = h // 2
    img = rounded_image(w, h, r, fill)
    if img is None:
        return tk.Label(parent, text=text, font=font, fg=fg, bg=fill,
                        padx=padx, pady=pady, bd=0)
    lbl = tk.Label(parent, text=text, image=img, compound="center",
                   font=font, fg=fg, bg=bg, bd=0)
    lbl._img = img
    return lbl


def set_pill(lbl, text, fill, fg, bg, font, padx=16, pady=6, r=None):
    """Reconfigura una píldora ya creada (cambia texto/color manteniendo el estilo)."""
    f = tkfont.Font(font=font)
    w = f.measure(text) + padx * 2
    h = f.metrics("linespace") + pady * 2
    if r is None:
        r = h // 2
    img = rounded_image(w, h, r, fill)
    if img is None:
        lbl.config(text=text, fg=fg, bg=fill)
        return
    lbl.config(text=text, image=img, compound="center", fg=fg, bg=bg)
    lbl._img = img


class RoundedButton(tk.Label):
    """Botón con fondo redondeado y estado hover. Estilo reconfigurable."""

    def __init__(self, parent, text, fill, fg, command, bg, font,
                 hover=None, padx=18, pady=8, r=12, ancho=None, borde=None, **kw):
        """`ancho` fija un ancho mínimo en píxeles. Sirve para que una fila de
        botones quede pareja aunque sus textos midan distinto."""
        self._text = text
        self._font = font
        self._bg = bg
        self._padx, self._pady, self._r = padx, pady, r
        self._ancho = ancho
        self._borde = borde
        self._command = command
        super().__init__(parent, cursor="hand2", bd=0, **kw)
        self.set_style(text=text, fill=fill, fg=fg, hover=hover)
        self.bind("<Button-1>", self._click)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)

    def set_style(self, text=None, fill=None, fg=None, hover=None, borde=False):
        """`borde` acepta None para quitarlo; por eso el defecto es False
        (= no tocar), y no None."""
        if borde is not False:
            self._borde = borde
        if text is not None:
            self._text = text
        if fill is not None:
            self._fill = fill
        if fg is not None:
            self._fg = fg
        if hover is not None:
            self._hover = hover
        f = tkfont.Font(font=self._font)
        w = f.measure(self._text) + self._padx * 2
        if self._ancho:
            w = max(w, self._ancho)
        h = f.metrics("linespace") + self._pady * 2
        self._img = rounded_image(w, h, self._r, self._fill, borde=self._borde)
        self._img_hover = rounded_image(w, h, self._r,
                                        getattr(self, "_hover", self._fill),
                                        borde=self._borde)
        if self._img is None:   # respaldo sin PIL
            self.config(text=self._text, font=self._font, fg=self._fg, bg=self._fill,
                        padx=self._padx, pady=self._pady, image="")
        else:
            self.config(text=self._text, image=self._img, compound="center",
                        font=self._font, fg=self._fg, bg=self._bg)

    def _click(self, _e=None):
        if self._command:
            self._command()

    def _enter(self, _e=None):
        if self._img_hover is not None:
            self.config(image=self._img_hover)

    def _leave(self, _e=None):
        if self._img is not None:
            self.config(image=self._img)


class RoundedPanel(tk.Frame):
    """Panel de tamaño fijo con fondo redondeado. El contenido va en `.inner`."""

    def __init__(self, parent, w, h, r, fill, bg, pad=None):
        super().__init__(parent, width=w, height=h, bg=bg)
        self.pack_propagate(False)
        self.grid_propagate(False)
        self.fill = fill
        img = rounded_image(w, h, r, fill)
        if img is None:   # respaldo sin PIL
            self.config(bg=fill, highlightbackground="#2b4066", highlightthickness=1)
            self.inner = tk.Frame(self, bg=fill)
            self.inner.pack(fill="both", expand=True, padx=r, pady=r)
        else:
            back = tk.Label(self, image=img, bg=bg, bd=0)
            back._img = img
            back.place(x=0, y=0, relwidth=1, relheight=1)
            p = r if pad is None else pad
            self.inner = tk.Frame(self, bg=fill)
            self.inner.place(x=p, y=p, width=w - 2 * p, height=h - 2 * p)
