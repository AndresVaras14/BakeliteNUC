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

# Familia tipográfica. Se ajusta al arrancar a una que exista de verdad en el
# equipo: DejaVu viene en Debian/Ubuntu pero no en Windows, y Tk sustituye en
# silencio por una cualquiera, con lo que la pantalla queda distinta sin avisar.
FAM = "DejaVu Sans"
FAMILIAS_PREFERIDAS = ("DejaVu Sans", "Noto Sans", "Liberation Sans",
                       "Segoe UI", "Arial", "Helvetica")

# Archivo .ttf para el texto con halo del veredicto (Pillow necesita el archivo,
# no el nombre de la familia). Se prueba en orden y se usa el primero que exista.
FUENTES_TTF = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",       # Debian/Ubuntu
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",                # Fedora/otros
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",                   # Arch
    "C:/Windows/Fonts/segoeuib.ttf",                              # Windows
    "C:/Windows/Fonts/arialbd.ttf",                               # Windows
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",          # macOS
)


def _ruta_fuente():
    """Primer .ttf disponible. None si no hay ninguno: entonces el veredicto se
    dibuja como texto plano, sin halo, en vez de fallar."""
    for ruta in FUENTES_TTF:
        if os.path.exists(ruta):
            return ruta
    return None


FUENTE_TTF = _ruta_fuente()

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


def _rut_visual(valor, vacio=""):
    """Única salida visual de RUT: puntos/guion y sin cero inicial."""
    return formatea_rut(valor) or vacio


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



# Los mixins copian a su propio espacio de nombres TODO lo que este modulo tenga
# definido en el momento de importarlos (ver la cabecera de cada uno). Por eso
# estos imports van al final: cualquier constante o funcion de modulo que se
# agregue MAS ABAJO de esta linea sera invisible para los mixins y reventara con
# NameError solo cuando se ejecute ese camino. Agregar siempre mas arriba.
from interfaz_componentes.construccion import ConstruccionMixin
from interfaz_componentes.debug import DebugMixin
from interfaz_componentes.dialogo_ajustes import DialogoAjustesMixin
from interfaz_componentes.estado_operacion import EstadoOperacionMixin
from interfaz_componentes.panel_conexiones import PanelConexionesMixin
from interfaz_componentes.recursos import RecursosMixin
from interfaz_componentes.torniquete import TorniqueteMixin


class Interfaz(
    ConstruccionMixin,
    TorniqueteMixin,
    DebugMixin,
    EstadoOperacionMixin,
    PanelConexionesMixin,
    DialogoAjustesMixin,
    RecursosMixin,
):
    """Fachada estable de la UI; los paneles viven en módulos especializados."""

    RUEDA = ("<MouseWheel>", "<Button-4>", "<Button-5>")
    ANCHO_BOTON = 132
    SECCIONES = {
        "terminal": ("Nombre terminal", "Nombre del terminal y ubicación"),
        "ajustes": ("Ajustes", "Lectoras y relés: cuál es entrada y cuál salida"),
        "pruebas": ("Pruebas", "Accionar torniquetes y luces del semáforo"),
    }

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

        global FAM
        disponibles = set(tkfont.families(self.root))
        for familia in FAMILIAS_PREFERIDAS:
            if familia in disponibles:
                if familia != FAM:
                    log.info("La familia %r no está instalada; se usa %r.", FAM, familia)
                FAM = familia
                break

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
        except Exception as e:  # noqa: BLE001
            log.debug("El entorno no permitió activar pantalla completa: %s", e)
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

