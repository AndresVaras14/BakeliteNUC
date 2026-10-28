# Montar el sistema en Debian

**Para qué:** dejar el terminal funcionando en un equipo Debian, como el que hay
hoy en producción.

**Lo que hay hoy en el NUC**, como referencia de que esta combinación funciona:

| | Versión |
| --- | --- |
| Sistema | Ubuntu 26.04 LTS |
| Python | 3.14.4 |
| pyserial | 3.5 · Pillow 12.1.1 · Tk 8.6 |

> El NUC actual es **Ubuntu**, no Debian. Para este sistema son equivalentes
> —Ubuntu es Debian— y no hay nada que dependa de la distribución exacta: la
> BD local es SQLite (incluida con Python), no hay servidor aparte que montar.

---

## 1. Paquetes del sistema

La vía recomendada detecta Linux y ejecuta el instalador de Debian/Ubuntu:

```bash
python3 instalar.py
```

```bash
sudo apt update
sudo apt install -y \
    python3 python3-pip python3-tk python3-serial python3-pil \
    fonts-dejavu-core \
    usbutils
```

Qué es cada cosa:

| Paquete | Para qué |
| --- | --- |
| `python3-tk` | La interfaz gráfica. **Sin esto la app no arranca.** |
| `python3-serial` | Hablar con las lectoras y el Arduino |
| `python3-pil` | Luces con degradado y texto con halo. Sin él, la app funciona con un respaldo más simple |
| `fonts-dejavu-core` | La tipografía de la pantalla |
| `usbutils` | `lsusb`, para diagnosticar el hardware |

Si prefieres instalar los paquetes de Python con pip en vez de apt:

```bash
pip3 install --break-system-packages -r requirements.txt
```

En Debian 12+ pip se niega a tocar los paquetes del sistema sin esa bandera. Lo
limpio es usar los de `apt` como arriba, o un entorno virtual:

```bash
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt
```

El `--system-site-packages` es necesario para que el entorno vea `tkinter`, que
no se instala con pip.

---

## 2. La base de datos

No hay nada que instalar ni crear a mano. `sqlite3` viene incluido con Python,
y `basedatos.py` deja listas las tablas y las filas mínimas (terminal,
lectoras, relés, estado de servicios) en `bakelite_nuc.db` la primera vez que
arranca la app, usando el número de torniquete que ya quedó definido en
`identidad_terminal.json` (ver `SISTEMA_NUC.md`).

Comprueba:

```bash
python3 -c "import basedatos; print(basedatos.BDLocal().terminal())"
```

---

## 3. Permisos del hardware

**Este es el paso que más se olvida.** Sin él la app no puede abrir los puertos
serie y las lectoras nunca responden:

```bash
sudo usermod -aG dialout $USER
```

Hay que **cerrar sesión y volver a entrar** para que tome efecto. Verifica:

```bash
id -nG | grep dialout
```

Comprueba que el sistema ve los aparatos:

```bash
ls -l /dev/ttyUSB* /dev/ttyACM*
lsusb
```

Deben aparecer tres: dos lectoras (chip CH340, `1a86:7523`) y el Arduino
(`2341:0043`). Los drivers vienen en el kernel; no hay que instalar nada.

---

## 4. La aplicación

```bash
cd ~/Escritorio/BakeliteNUC
python3 main.py
```

Para el arranque a prueba de caídas, que es como debe quedar en producción:

```bash
python3 supervisor.py
```

El supervisor relanza la app si se cae, espera más si entra en crash-loop y deja
**cada caída registrada en la bitácora local**, no solo en el archivo de log.

### Arranque automático

Hoy en el NUC se lanza a mano. Para que arranque solo con la sesión gráfica:

```bash
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/bakelite.desktop <<'FIN'
[Desktop Entry]
Type=Application
Name=Bakelite Control de Acceso
Exec=/usr/bin/python3 /home/USUARIO/Escritorio/BakeliteNUC/supervisor.py
Path=/home/USUARIO/Escritorio/BakeliteNUC
X-GNOME-Autostart-enabled=true
FIN
```

Reemplaza `USUARIO` por el tuyo.

> **No uses un servicio de systemd** para esto: la app necesita una sesión
> gráfica con `DISPLAY`, y un servicio del sistema no la tiene. Si algún día se
> quiere sin escritorio, habría que levantar un X mínimo.

También conviene **desactivar el apagado de pantalla**, o el torniquete se queda
en negro:

```bash
gsettings set org.gnome.desktop.session idle-delay 0
gsettings set org.gnome.desktop.screensaver lock-enabled false
```

---

## 5. Comprobar que quedó bien

En orden. Si uno falla, no sigas al siguiente.

```bash
# 1. Python con interfaz gráfica
python3 -c "import tkinter; print('tk', tkinter.TkVersion)"

# 2. Los paquetes
python3 -c "import serial, sqlite3, PIL; print('paquetes ok')"

# 3. Permiso de puertos
id -nG | grep -o dialout

# 4. El hardware está enchufado
ls /dev/ttyUSB* /dev/ttyACM*

# 5. La base responde (se crea sola la primera vez)
python3 -c "import basedatos; print(basedatos.BDLocal().terminal())"

# 6. Qué aparatos reconoce la app
python3 -c "import deteccion_puertos; print(deteccion_puertos.detectar({}))"

# 7. La app
python3 main.py
```

El paso 6 debe mostrar los tres aparatos con su ruta. Si alguna lectora sale
`None` pero aparece en `ls`, revisa §3 y §7.

---

## 6. Si algo no funciona

| Síntoma | Causa habitual |
| --- | --- |
| `No module named tkinter` | Falta `python3-tk` |
| `numero_torniquete cambió...` al arrancar | Alguien editó `identidad_terminal.json` con un número distinto al ya bloqueado. Ver `identidad_terminal.py` |
| Las lectoras aparecen en `ls` pero la app no las usa | Falta el grupo `dialout`, o no cerraste sesión (§3) |
| `Permission denied: /dev/ttyUSB0` | Lo mismo |
| El Arduino se detecta como lectora | Es un clon con chip CH340: comparte el `1a86`. Ver `ESPECIFICACION_HARDWARE.md` §2 |
| La app arranca pero "sin conexión a Bakelite" | Normal sin internet. El acceso funciona y las marcas se encolan |
| La pantalla se apaga sola | Falta desactivar el salvapantallas (§4) |
| `externally-managed-environment` al usar pip | Debian 12+: usa los paquetes de `apt` o un venv (§1) |

Para ver qué está pasando por dentro:

```bash
tail -f logs/app.log        # todo
tail -f logs/errores.log    # solo errores
```

Y dentro de la app, **Ajustes → Diagnóstico → Modo debugger** parte la pantalla y
muestra paso a paso lo que se hace y lo que se recibe.

---

## 7. Resumen de lo que hay que instalar

| Sí | No |
| --- | --- |
| `python3-tk`, `python3-serial`, `python3-pil` | Un servidor web: la app no expone nada |
| `fonts-dejavu-core` | Docker |
| El grupo `dialout` | SQL Server, ODBC ni ningún motor de BD aparte: es SQLite |
| | Drivers de las lectoras: vienen en el kernel |
| | El IDE de Arduino |
| | Nada relacionado con `udev`: el sistema ya lo trae |
