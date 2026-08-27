#!/usr/bin/env bash
# =============================================================================
# Instalador del control de acceso Bakelite — Debian / Ubuntu
#
#   ./instalar_linux.sh              instala todo
#   ./instalar_linux.sh --verificar  solo comprueba, no toca nada
#   ./instalar_linux.sh --sin-bd     omite la base de datos
#
# Es idempotente: se puede correr las veces que haga falta.
# =============================================================================
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

VERDE=$'\e[32m'; ROJO=$'\e[31m'; AMAR=$'\e[33m'; AZUL=$'\e[36m'; FIN=$'\e[0m'
SOLO_VERIFICAR=0
SIN_BD=0
FALLOS=0
AVISOS=0

for arg in "$@"; do
    case "$arg" in
        --verificar) SOLO_VERIFICAR=1 ;;
        --sin-bd)    SIN_BD=1 ;;
        -h|--help)   sed -n '2,10p' "$0"; exit 0 ;;
        *) echo "Opción desconocida: $arg"; exit 1 ;;
    esac
done

paso()  { echo; echo "${AZUL}=== $* ===${FIN}"; }
ok()    { echo "  ${VERDE}✔${FIN} $*"; }
falla() { echo "  ${ROJO}x${FIN} $*"; FALLOS=$((FALLOS+1)); }
aviso() { echo "  ${AMAR}!${FIN} $*"; AVISOS=$((AVISOS+1)); }

# Corre algo solo si no estamos en modo verificación.
hacer() {
    if [ "$SOLO_VERIFICAR" = 1 ]; then
        echo "  ${AMAR}·${FIN} (verificación) se omitiría: $*"
        return 0
    fi
    "$@"
}

echo "${AZUL}Instalador del control de acceso Bakelite${FIN}"
echo "Carpeta: $DIR"
[ "$SOLO_VERIFICAR" = 1 ] && echo "${AMAR}Modo verificación: no se modifica nada.${FIN}"

# -----------------------------------------------------------------------------
paso "1. El sistema"
if [ -r /etc/os-release ]; then
    . /etc/os-release
    ok "$PRETTY_NAME"
    case "${ID_LIKE:-$ID}" in
        *debian*|*ubuntu*) ;;
        *) aviso "Esta guía asume Debian o Ubuntu; en $ID puede que los paquetes se llamen distinto." ;;
    esac
else
    aviso "No se pudo identificar la distribución."
fi
[ "$(id -u)" = "0" ] && aviso "Se está ejecutando como root: el arranque automático y el grupo dialout se aplicarán al usuario root, no al que usará el equipo."

# -----------------------------------------------------------------------------
paso "2. Paquetes del sistema"
PAQUETES=(python3 python3-pip python3-tk python3-serial python3-pil
          fonts-dejavu-core unixodbc tdsodbc freetds-common usbutils)
FALTAN=()
for p in "${PAQUETES[@]}"; do
    if dpkg -s "$p" >/dev/null 2>&1; then ok "$p"; else FALTAN+=("$p"); fi
done
if [ ${#FALTAN[@]} -gt 0 ]; then
    echo "  Faltan: ${FALTAN[*]}"
    if hacer sudo apt-get update -qq && hacer sudo apt-get install -y "${FALTAN[@]}"; then
        [ "$SOLO_VERIFICAR" = 0 ] && ok "Instalados"
    else
        falla "No se pudieron instalar. Revisa la conexión o los permisos de sudo."
    fi
fi

# -----------------------------------------------------------------------------
paso "3. Python y sus módulos"
if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)"; then
    ok "Python $(python3 --version | cut -d' ' -f2)"
else
    falla "Se necesita Python 3.9 o superior."
fi
for m in tkinter serial pyodbc PIL; do
    if python3 -c "import $m" 2>/dev/null; then
        ok "módulo $m"
    elif [ "$m" = "pyodbc" ]; then
        echo "  Falta pyodbc; se instala con pip."
        hacer python3 -m pip install --break-system-packages -q pyodbc \
            && ok "pyodbc instalado" || falla "No se pudo instalar pyodbc."
    elif [ "$m" = "PIL" ]; then
        aviso "Pillow no está: la pantalla funciona con un respaldo más simple."
    else
        falla "Falta el módulo $m (paquete python3-${m,,})."
    fi
done

# -----------------------------------------------------------------------------
paso "4. Driver ODBC"
DRIVERS=$(python3 -c "import pyodbc; print(', '.join(pyodbc.drivers()))" 2>/dev/null)
if [ -n "$DRIVERS" ]; then
    ok "Disponibles: $DRIVERS"
else
    falla "No hay ningún driver ODBC. Instala tdsodbc o msodbcsql18."
fi

# -----------------------------------------------------------------------------
paso "5. Permiso de los puertos serie"
if id -nG | tr ' ' '\n' | grep -qx dialout; then
    ok "El usuario $(id -un) ya está en el grupo dialout"
else
    if hacer sudo usermod -aG dialout "$(id -un)"; then
        [ "$SOLO_VERIFICAR" = 0 ] && aviso "Agregado al grupo dialout: HAY QUE CERRAR SESIÓN para que tome efecto."
    else
        falla "No se pudo agregar al grupo dialout. Sin esto las lectoras no responden."
    fi
fi

# -----------------------------------------------------------------------------
paso "6. Hardware conectado"
PUERTOS=$(ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null)
if [ -n "$PUERTOS" ]; then
    for d in $PUERTOS; do ok "$d"; done
    if python3 -c "import serial" 2>/dev/null; then
        python3 - <<'PY' 2>/dev/null || aviso "No se pudo clasificar el hardware."
import sys; sys.path.insert(0, ".")
import deteccion_puertos
r = deteccion_puertos.detectar({})
for clave in ("arduino", "lectora1", "lectora2"):
    v = r.get(clave)
    print(f"  {'✔' if v else '!'} {clave}: {v or 'no detectado'}")
PY
    fi
else
    aviso "No hay ningún puerto serie. Enchufa las lectoras y el Arduino."
fi

# -----------------------------------------------------------------------------
paso "7. Base de datos"
if [ "$SIN_BD" = 1 ]; then
    aviso "Omitida por --sin-bd."
elif python3 -c "
import sys; sys.path.insert(0, '.')
import basedatos
bd = basedatos.BDLocal()
t = bd.terminal()
sys.exit(0 if t else 1)" 2>/dev/null; then
    ok "La base responde y el terminal está configurado"
else
    aviso "La base no responde todavía."
    echo "     Para crearla, con el motor de SQL Server corriendo:"
    echo "       sqlcmd -S localhost -U sa -P 'TU_CLAVE' -C -i bd/crear_bd_completa.sql"
    echo "     La app arranca igual sin base: las marcas se encolan en registros.json."
fi

# -----------------------------------------------------------------------------
paso "8. Arranque automático y reinicio ante caídas"
# supervisor.py relanza la app si se cae; el autostart la levanta al iniciar
# sesión. Los dos juntos son lo que hace que el terminal se recupere solo.
AUTOSTART="$HOME/.config/autostart/bakelite.desktop"
if [ "$SOLO_VERIFICAR" = 1 ]; then
    [ -f "$AUTOSTART" ] && ok "Ya configurado: $AUTOSTART" || aviso "No configurado."
else
    mkdir -p "$HOME/.config/autostart"
    cat > "$AUTOSTART" <<FIN
[Desktop Entry]
Type=Application
Name=Bakelite Control de Acceso
Comment=Se levanta al iniciar sesión; supervisor.py lo relanza si se cae
Exec=/usr/bin/python3 $DIR/supervisor.py
Path=$DIR
Terminal=false
X-GNOME-Autostart-enabled=true
FIN
    ok "Arranque automático: $AUTOSTART"
    ok "Reinicio ante caídas: lo hace supervisor.py"
fi

# La pantalla no debe apagarse: es un torniquete, no un escritorio.
if command -v gsettings >/dev/null 2>&1; then
    if [ "$SOLO_VERIFICAR" = 0 ]; then
        gsettings set org.gnome.desktop.session idle-delay 0 2>/dev/null \
            && ok "Apagado de pantalla desactivado"
        gsettings set org.gnome.desktop.screensaver lock-enabled false 2>/dev/null
    fi
fi

# -----------------------------------------------------------------------------
paso "Resumen"
if [ "$FALLOS" -eq 0 ]; then
    echo "  ${VERDE}Sin fallos${FIN}${AVISOS:+, $AVISOS aviso(s)}"
    echo
    echo "  Para arrancar ahora:"
    echo "     cd $DIR && python3 supervisor.py"
    [ "$SOLO_VERIFICAR" = 0 ] && echo "  Al reiniciar el equipo arrancará solo."
else
    echo "  ${ROJO}$FALLOS fallo(s)${FIN} y $AVISOS aviso(s). Revisa lo marcado arriba."
fi
exit $(( FALLOS > 0 ? 1 : 0 ))
