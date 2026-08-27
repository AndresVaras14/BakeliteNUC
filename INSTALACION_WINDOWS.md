# Montar el sistema en Windows para desarrollo

**Para qué:** seguir desarrollando y probando en un equipo Windows. El destino
final del sistema es Linux; esto es solo el entorno de trabajo.

**Lo que hay hoy en el NUC**, para tenerlo como referencia:

| | Versión |
| --- | --- |
| Python | 3.14.4 |
| SQL Server | 2025 (17.0.4075.5) |
| pyserial | 3.5 |
| Pillow | 12.1.1 |
| Tk | 8.6 |

---

## 1. Lo que SÍ hay que instalar

### 1.1 Python

Descarga **Python 3.11 o superior** de [python.org](https://www.python.org/downloads/windows/).

Al instalar, marca:

- ☑ **Add python.exe to PATH**
- ☑ **tcl/tk and IDLE** — está marcado por defecto y **no lo desmarques**: sin
  eso no hay interfaz gráfica y la app no arranca.

Verifica:

```bat
python --version
python -c "import tkinter; print(tkinter.TkVersion)"
```

Si el segundo comando falla, reinstala Python con la opción tcl/tk marcada.

### 1.2 SQL Server

**SQL Server Express** o **Developer** desde
[la página de Microsoft](https://www.microsoft.com/sql-server/sql-server-downloads).
Developer es gratuito y trae todo; Express alcanza de sobra.

En el instalador, elige **Personalizada → Instalación independiente** y:

- **Tipo de autenticación: modo mixto** (SQL + Windows). Es obligatorio: la app
  se conecta con usuario y contraseña, no con autenticación de Windows.
- Define y anota la clave de **sa**: la vas a necesitar para el script.

Después de instalar, **habilita TCP/IP**, que viene apagado:

1. Abre *SQL Server Configuration Manager*.
2. *Configuración de red de SQL Server* → *Protocolos de <instancia>*.
3. **TCP/IP** → clic derecho → **Habilitar**.
4. Doble clic en TCP/IP → pestaña *Direcciones IP* → al final, en **IPAll**,
   deja `Puerto TCP = 1433` y borra *Puertos dinámicos*.
5. Reinicia el servicio *SQL Server*.

> Sin este paso la app no conecta aunque el motor esté corriendo, y el error que
> da no dice nada sobre TCP.

### 1.3 Driver ODBC

**ODBC Driver 18 for SQL Server**, desde
[aquí](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server).

Verifica que quedó instalado:

```bat
python -c "import pyodbc; print(pyodbc.drivers())"
```

Tiene que aparecer `ODBC Driver 18 for SQL Server`. El código lo elige solo.

### 1.4 Paquetes de Python

Desde la carpeta del proyecto:

```bat
python -m pip install -r requirements.txt
```

Son tres: `pyserial`, `pyodbc` y `Pillow`.

### 1.5 Drivers de los aparatos

| Aparato | Driver |
| --- | --- |
| **Lectoras** (chip CH340) | Driver CH340 de WCH. Windows a veces lo instala solo; si en el Administrador de dispositivos aparece un dispositivo desconocido, instálalo a mano. |
| **Arduino Uno** | Viene con el IDE de Arduino, o se instala solo si es original. |

Comprueba en el **Administrador de dispositivos → Puertos (COM y LPT)** que
aparezcan los tres: dos lectoras y el Arduino, cada uno con su COM.

---

## 2. Lo que NO hay que instalar

| No instales | Por qué |
| --- | --- |
| **FreeTDS / unixODBC** | Son el equivalente Linux del driver ODBC. En Windows sobran. |
| **Fuentes DejaVu** | La app ahora busca entre varias familias y usa Segoe UI o Arial si no encuentra DejaVu. |
| **WSL** | No hace falta: el código corre nativo en Windows. |
| **Un servidor web** | La app no expone nada; solo hace peticiones salientes. |
| **Docker** | Nada del sistema está contenerizado. |
| Cualquier cosa relacionada con `udev` | Es específico de Linux; el código ya tiene su camino alternativo. |

---

## 3. Crear la base de datos

Con SQL Server instalado, ejecuta el script **como `sa`**:

```bat
sqlcmd -S localhost -U sa -P TuClaveDeSa -i bd\crear_bd_completa.sql
```

O ábrelo en *SQL Server Management Studio*, conéctate como `sa` y ejecútalo.

El script hace todo solo:

1. crea la base `BakeliteTorniquete`;
2. crea el login y el usuario **`userBakelite`** con clave `bakelite123` y sus
   permisos de lectura y escritura;
3. crea las 11 tablas con sus claves, restricciones e índices;
4. inserta las filas mínimas: el terminal, la versión, los servicios vigilados y
   las dos lectoras y dos relés.

Es idempotente: puedes correrlo de nuevo sin romper nada.

> **Los permisos son a propósito acotados.** `userBakelite` puede leer y escribir
> datos pero **no cambiar la estructura**: las migraciones se ejecutan con `sa`.
> Así un fallo del software no puede alterar el esquema.

Comprueba que la app ve la base:

```bat
python -c "import basedatos; print(basedatos.BDLocal().terminal())"
```

Debe imprimir el diccionario del terminal 1. Si dice que no hay conexión, revisa
TCP/IP (§1.2) y el driver ODBC (§1.3).

---

## 4. Configuración

Todo vive en `config.py`. Para desarrollo normalmente **no hay que tocar nada**,
salvo estos casos:

| Si… | Cambia |
| --- | --- |
| SQL Server está en otra máquina o instancia | `SQL_SERVIDOR` (ej. `"localhost\\SQLEXPRESS"`) |
| Usaste otra clave para `userBakelite` | `SQL_CLAVE`, y la del script |
| Quieres probar sin tocar la API real | `SIMULAR_API = True` |
| No tienes SQL Server a mano | `USAR_BD_LOCAL = False` — la app funciona, encolando en `registros.json` |

También se pueden usar variables de entorno, que ganan sobre `config.py`:

```bat
set BAKELITE_SQL_SERVIDOR=localhost
set BAKELITE_SQL_CLAVE=bakelite123
```

> **Cuidado con `ID_TERMINAL`.** Si desarrollas contra la API de producción con
> `ID_TERMINAL = 1`, tu equipo de pruebas va a pelear con el NUC real por el
> mismo terminal: ambos mandarán heartbeat y configuración de dispositivos, y el
> último en escribir gana. Para desarrollar contra la API real, **pide un
> idTerminal aparte**.

---

## 5. Arrancar

```bat
python main.py
```

Para el arranque a prueba de caídas, el mismo que se usa en el equipo real:

```bat
python supervisor.py
```

Sin hardware conectado la app arranca igual: muestra qué falta y funciona el
modo simulación (teclas `1`–`6` para entrada, `Ctrl+1`–`6` para salida).

---

## 6. Diferencias reales con Linux

Estas son las que importan al probar. Todo lo demás se comporta igual.

### 6.1 Los puertos

En Linux son `/dev/ttyUSB0`; en Windows, `COM3`. El código lo resuelve solo:
detecta con `udevadm` en Linux y con `pyserial` en Windows.

### 6.2 La identificación de cada lectora

Las dos lectoras son CH340 idénticas y **no tienen número de serie**, así que se
anclan al **zócalo USB físico**:

| | Ancla |
| --- | --- |
| Linux | `ID_PATH` de udev |
| Windows | `LOCATION` del `hwid` de pyserial |

Ambas funcionan igual: si desenchufas una, se informa esa y la otra conserva su
número. **Si cambias una lectora de puerto USB, cambia su ancla** y tomará el
número que esté libre; para eso está el botón *Identificar lectora* en Ajustes.

### 6.3 ⚠️ El Arduino puede no ser detectado

Este es el punto con más riesgo. La clasificación busca la palabra `arduino` en
la descripción del puerto. Con el driver oficial Windows lo llama *"Arduino Uno
(COM5)"* y funciona; pero con un **driver genérico**, o con un **clon**, aparece
como *"USB Serial Device"* y **no se detecta**: no habría relés ni luces.

Si te pasa, agrega el identificador del fabricante en `deteccion_puertos.py`:

```python
PALABRAS_ARDUINO = ["arduino", "2341"]     # 2341 = VID de Arduino
```

Un clon con chip CH340 es peor: se clasifica como **lectora**, porque comparte
el `1a86` de las lectoras. Está advertido en `ESPECIFICACION_HARDWARE.md` §2.

### 6.4 La tipografía

En Linux se usa DejaVu Sans. En Windows no existe, así que la app elige la
primera disponible entre Noto Sans, Liberation Sans, **Segoe UI** o Arial.

El veredicto grande (`AUTORIZADO` / `DENEGADO`) se dibuja con halo usando un
archivo `.ttf`: en Windows toma `segoeuib.ttf` o `arialbd.ttf`. Si no encontrara
ninguno, cae a texto plano sin halo — se ve distinto pero **no falla**.

Consecuencia práctica: **la pantalla se verá levemente distinta a la del NUC**.
No es un error; no persigas esa diferencia.

### 6.5 El supervisor

Funciona igual, pero en Windows se ejecuta en una consola. Para el equipo real
en Linux se usa systemd o el arranque de sesión; en Windows sería una tarea
programada. Para desarrollar, la consola basta.

### 6.6 Permisos de puerto

En Linux hay que agregar el usuario al grupo `dialout`. **En Windows no hace
falta nada**: los puertos COM son accesibles.

---

## 7. Comprobar que quedó bien

En orden. Si uno falla, no sigas al siguiente.

```bat
:: 1. Python con interfaz gráfica
python -c "import tkinter; print('tk', tkinter.TkVersion)"

:: 2. Los tres paquetes
python -c "import serial, pyodbc, PIL; print('paquetes ok')"

:: 3. El driver ODBC
python -c "import pyodbc; print(pyodbc.drivers())"

:: 4. La base responde
python -c "import basedatos; print(basedatos.BDLocal().terminal())"

:: 5. Los dispositivos configurados
python -c "import basedatos; bd=basedatos.BDLocal(); print(bd.lectoras()); print(bd.reles())"

:: 6. Qué hardware ve
python -c "import deteccion_puertos; print(deteccion_puertos.detectar({}))"

:: 7. La app
python main.py
```

El paso 6 debe mostrar los tres aparatos. Si `arduino` sale como `None` pero en
el Administrador de dispositivos aparece, lee §6.3.

---

## 8. Si algo no funciona

| Síntoma | Causa habitual |
| --- | --- |
| `No module named tkinter` | Python instalado sin tcl/tk. Reinstalar marcando esa opción. |
| `Data source name not found` | Falta el ODBC Driver 18 (§1.3). |
| `Login failed for user 'userBakelite'` | El modo mixto no está activo, o la clave no coincide con `config.SQL_CLAVE`. |
| Conecta desde SSMS pero no desde Python | Falta habilitar TCP/IP (§1.2). |
| `pyodbc.OperationalError` al arrancar | El servicio SQL Server está detenido. |
| La app arranca pero "sin conexión a Bakelite" | Normal si no hay internet. El acceso funciona igual y las marcas se encolan. |
| No aparece ninguna lectora | Falta el driver CH340, o el cable es solo de carga. |
| El Arduino no se detecta | §6.3 |
| La pantalla se ve distinta al NUC | Es la tipografía (§6.4). No es un error. |

---

## 9. Antes de volver a Linux

Lo que cambies en Windows funciona igual en Linux **salvo** que toques:

- `deteccion_puertos.py` — tiene dos caminos, uno por sistema operativo;
- rutas de archivos — usa siempre `config.ruta()`, nunca rutas con `C:\`;
- `FUENTES_TTF` en `interfaz.py` — si agregas una ruta de Windows, deja las de
  Linux primero.

Y lo más importante: **prueba en el NUC antes de dar algo por terminado.** El
hardware real, los tiempos de los puertos serie y el comportamiento de las
lectoras solo se ven ahí.
