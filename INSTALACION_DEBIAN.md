# Montar el sistema en Debian

**Para qué:** dejar el terminal funcionando en un equipo Debian, como el que hay
hoy en producción.

**Lo que hay hoy en el NUC**, como referencia de que esta combinación funciona:

| | Versión |
| --- | --- |
| Sistema | Ubuntu 26.04 LTS |
| Python | 3.14.4 |
| SQL Server | 2025 (17.0.4075.5), corriendo **en el mismo equipo** |
| Driver ODBC | **FreeTDS** 1.5.5 (no el de Microsoft) |
| pyserial | 3.5 · Pillow 12.1.1 · Tk 8.6 |

> El NUC actual es **Ubuntu**, no Debian. Casi todo es idéntico —Ubuntu es
> Debian— salvo un punto importante que está en §3: el motor de SQL Server.

---

## 1. Paquetes del sistema

La vía recomendada detecta Linux y ejecuta el instalador de Debian/Ubuntu:

```bash
python3 instalar.py
```

En Linux `pymssql` no se instala: la aplicación mantiene `pyodbc` con FreeTDS o
el driver ODBC de Microsoft.

```bash
sudo apt update
sudo apt install -y \
    python3 python3-pip python3-tk python3-serial python3-pil \
    fonts-dejavu-core \
    unixodbc tdsodbc freetds-common \
    usbutils
```

Qué es cada cosa:

| Paquete | Para qué |
| --- | --- |
| `python3-tk` | La interfaz gráfica. **Sin esto la app no arranca.** |
| `python3-serial` | Hablar con las lectoras y el Arduino |
| `python3-pil` | Luces con degradado y texto con halo. Sin él, la app funciona con un respaldo más simple |
| `fonts-dejavu-core` | La tipografía de la pantalla |
| `unixodbc` + `tdsodbc` | El driver ODBC para SQL Server (ver §2) |
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

## 2. El driver ODBC

Hay dos caminos. **El que está probado en el NUC es FreeTDS**, que viene en los
repositorios de Debian y ya quedó instalado en §1.

```bash
python3 -c "import pyodbc; print(pyodbc.drivers())"
```

Debe imprimir `['FreeTDS']`. El código lo detecta solo: busca primero el driver
de Microsoft y, si no está, usa FreeTDS.

**Alternativa: el driver oficial de Microsoft** (`msodbcsql18`). Microsoft sí
publica paquetes para Debian:

```bash
curl -sSL https://packages.microsoft.com/keys/microsoft.asc \
  | sudo tee /etc/apt/trusted.gpg.d/microsoft.asc > /dev/null
curl -sSL https://packages.microsoft.com/config/debian/12/prod.list \
  | sudo tee /etc/apt/sources.list.d/mssql-release.list
sudo apt update
sudo ACCEPT_EULA=Y apt install -y msodbcsql18
```

Ajusta el `12` a tu versión de Debian. No es obligatorio: FreeTDS funciona y es
lo que está en producción.

> Un detalle técnico que ya está resuelto en el código: ni FreeTDS ni pyodbc
> saben leer el tipo `DATETIMEOFFSET`, que la app usa para las fechas de
> sincronización. `basedatos.py` registra un conversor propio. No hay que hacer
> nada, pero si ves fechas raras en otro cliente, es eso.

---

## 3. SQL Server

⚠️ **Microsoft no soporta oficialmente el motor de SQL Server sobre Debian.**
Las distribuciones soportadas son Ubuntu, RHEL y SLES. Tienes tres opciones:

### Opción A — Ubuntu Server en vez de Debian (lo recomendado)

Es lo que corre hoy en el NUC y lo único probado de punta a punta:

```bash
curl -sSL https://packages.microsoft.com/keys/microsoft.asc \
  | sudo tee /etc/apt/trusted.gpg.d/microsoft.asc > /dev/null
curl -sSL https://packages.microsoft.com/config/ubuntu/24.04/mssql-server-2022.list \
  | sudo tee /etc/apt/sources.list.d/mssql-server.list
sudo apt update
sudo apt install -y mssql-server
sudo /opt/mssql/bin/mssql-conf setup
```

El `setup` pide la edición (**Express** o **Developer**, ambas gratuitas) y la
clave de `sa`.

### Opción B — SQL Server en un contenedor sobre Debian

```bash
sudo apt install -y docker.io
sudo docker run -e "ACCEPT_EULA=Y" -e "MSSQL_SA_PASSWORD=TuClaveFuerte1!" \
   -p 1433:1433 --name bakelite-sql --restart unless-stopped \
   -v bakelite-sql-data:/var/opt/mssql \
   -d mcr.microsoft.com/mssql/server:2022-latest
```

El volumen es lo que hace que los datos sobrevivan a recrear el contenedor. Para
la app es indistinguible: sigue conectándose a `localhost:1433`.

### Opción C — Un SQL Server en otra máquina

Deja Debian solo con la app y apunta `SQL_SERVIDOR` al servidor real.

> **La app funciona sin BD local.** Con `USAR_BD_LOCAL = False` sigue leyendo
> cédulas, abriendo el torniquete y encolando las marcas en `bakelite_nuc.db`.
> Pierdes el historial local y la configuración persistente, pero el control de
> acceso opera. Sirve para levantar el equipo mientras resuelves la base.

---

## 4. Crear la base de datos

Con el motor corriendo, ejecuta el script **como `sa`**:

```bash
# Si instalaste las herramientas: sudo apt install -y mssql-tools18
/opt/mssql-tools18/bin/sqlcmd -S localhost -U sa -P 'TuClaveDeSa' \
    -C -i bd/crear_bd_completa.sql
```

El `-C` acepta el certificado autofirmado del servidor, que es lo normal en una
instalación local.

El script crea la base, el usuario `userBakelite` con sus permisos, las 11 tablas
y las filas mínimas. Es idempotente.

Comprueba:

```bash
python3 -c "import basedatos; print(basedatos.BDLocal().terminal())"
```

---

## 5. Permisos del hardware

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

## 6. La aplicación

```bash
cd ~/Escritorio/BakeliteNUC
python3 main.py
```

Para el arranque a prueba de caídas, que es como debe quedar en producción:

```bash
python3 supervisor.py
```

El supervisor relanza la app si se cae, espera más si entra en crash-loop y deja
**cada caída registrada en la BD**, no solo en el archivo de log.

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

## 7. Comprobar que quedó bien

En orden. Si uno falla, no sigas al siguiente.

```bash
# 1. Python con interfaz gráfica
python3 -c "import tkinter; print('tk', tkinter.TkVersion)"

# 2. Los tres paquetes
python3 -c "import serial, sqlite3, pyodbc, PIL; print('paquetes ok')"

# 3. El driver ODBC
python3 -c "import pyodbc; print(pyodbc.drivers())"

# 4. Permiso de puertos
id -nG | grep -o dialout

# 5. El hardware está enchufado
ls /dev/ttyUSB* /dev/ttyACM*

# 6. La base responde
python3 -c "import basedatos; print(basedatos.BDLocal().terminal())"

# 7. Qué aparatos reconoce la app
python3 -c "import deteccion_puertos; print(deteccion_puertos.detectar({}))"

# 8. La app
python3 main.py
```

El paso 7 debe mostrar los tres aparatos con su ruta. Si alguna lectora sale
`None` pero aparece en `ls`, revisa §5 y §8.

---

## 8. Si algo no funciona

| Síntoma | Causa habitual |
| --- | --- |
| `No module named tkinter` | Falta `python3-tk` |
| `Data source name not found` | Falta `tdsodbc` o `msodbcsql18` (§2) |
| `Login failed for user 'userBakelite'` | La clave no coincide con `config.SQL_CLAVE` |
| `Login timeout expired` | El motor no está corriendo: `systemctl status mssql-server` |
| Las lectoras aparecen en `ls` pero la app no las usa | Falta el grupo `dialout`, o no cerraste sesión (§5) |
| `Permission denied: /dev/ttyUSB0` | Lo mismo |
| El Arduino se detecta como lectora | Es un clon con chip CH340: comparte el `1a86`. Ver `ESPECIFICACION_HARDWARE.md` §2 |
| La app arranca pero "sin conexión a Bakelite" | Normal sin internet. El acceso funciona y las marcas se encolan |
| La pantalla se apaga sola | Falta desactivar el salvapantallas (§6) |
| `externally-managed-environment` al usar pip | Debian 12+: usa los paquetes de `apt` o un venv (§1) |

Para ver qué está pasando por dentro:

```bash
tail -f logs/app.log        # todo
tail -f logs/errores.log    # solo errores
```

Y dentro de la app, **Ajustes → Diagnóstico → Modo debugger** parte la pantalla y
muestra paso a paso lo que se hace y lo que se recibe.

---

## 9. Resumen de lo que hay que instalar

| Sí | No |
| --- | --- |
| `python3-tk`, `python3-serial`, `python3-pil` | Un servidor web: la app no expone nada |
| `unixodbc` + `tdsodbc` (o `msodbcsql18`) | Docker, salvo que uses la opción B de §3 |
| `fonts-dejavu-core` | Drivers de las lectoras: vienen en el kernel |
| SQL Server (§3) | El IDE de Arduino |
| El grupo `dialout` | Nada relacionado con `udev`: el sistema ya lo trae |
