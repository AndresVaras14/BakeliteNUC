# -*- coding: utf-8 -*-
"""Panel de conexiones y detección de hardware."""

import interfaz as _base

globals().update({nombre: getattr(_base, nombre) for nombre in dir(_base)
                  if not nombre.startswith("__")})

class PanelConexionesMixin:
    """Estado físico, redetección y datos visibles del terminal."""

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
            except Exception as e:  # noqa: BLE001  el diálogo pudo cerrarse mientras tanto
                log.debug("No se actualizó el diálogo de estado ya cerrado: %s", e)

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

    def _ui_lectora_identificada(self, numero, rut, aj):
        """Una lectora acaba de leer: se resalta su fila y se dice cuál es."""
        rut = _rut_visual(rut)
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
        except Exception as e:  # noqa: BLE001  el diálogo pudo cerrarse mientras tanto
            log.debug("No se actualizó el diálogo de identificación ya cerrado: %s", e)

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

