# Montar el sistema en Windows para desarrollo

**Para qué:** seguir desarrollando y probando en un equipo Windows. El destino
final del sistema es Linux; esto es solo el entorno de trabajo.

**Lo que hay hoy en el NUC**, para tenerlo como referencia:

| | Versión |
| --- | --- |
| Python | 3.14.4 |
| pyserial | 3.5 |
| Pillow | 12.1.1 |
| Tk | 8.6 |

La BD local es SQLite (incluida con Python): no hay servidor que instalar ni
driver que configurar. Ver `SISTEMA_NUC.md`.

---

## 1. Lo que SÍ hay que instalar

Para detectar Windows, usar el Python actual e instalar/verificar todo con el
flujo correcto, ejecuta desde la carpeta del proyecto:

```bat
python instalar.py
```

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

### 1.2 Paquetes de Python

Desde la carpeta del proyecto:

```bat
python -m pip install -r requirements.txt
```

En Windows instala `pyserial` y `Pillow`. `sqlite3` ya viene con Python, no
hace falta instalar nada más para la BD local.

### 1.3 Drivers de los aparatos

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
| **SQL Server, ODBC o cualquier motor de BD aparte** | La BD local es SQLite, incluida con Python. |
| **Fuentes DejaVu** | La app ahora busca entre varias familias y usa Segoe UI o Arial si no encuentra DejaVu. |
| **WSL** | No hace falta: el código corre nativo en Windows. |
| **Un servidor web** | La app no expone nada; solo hace peticiones salientes. |
| **Docker** | Nada del sistema está contenerizado. |
| Cualquier cosa relacionada con `udev` | Es específico de Linux; el código ya tiene su camino alternativo. |

---

## 3. La base de datos

No hay nada que crear a mano: la primera vez que arranca la app, `basedatos.py`
deja listas las tablas y las filas mínimas (terminal, lectoras, relés, estado
de servicios) directamente en `bakelite_nuc.db` (SQLite), usando el número de
torniquete que ya quedó definido en `identidad_terminal.json`.

Comprueba que la app ve la base:

```bat
python -c "import basedatos; print(basedatos.BDLocal().terminal())"
```

Debe imprimir el diccionario del terminal.

---

## 4. Configuración

Todo vive en `config.py` (más `identidad_terminal.json` para el número de
torniquete y la dirección de la API — ver `SISTEMA_NUC.md`). Para desarrollo
normalmente **no hay que tocar nada**, salvo:

| Si… | Cambia |
| --- | --- |
| Quieres probar sin tocar la API real | `SIMULAR_API = True` |
| Vas a usar una dirección de API distinta | `direccion_api` en `identidad_terminal.json` |

> **Cuidado con el número de torniquete.** Si desarrollas contra la API de producción
> con el mismo `numero_torniquete` que un NUC real, tu equipo de pruebas va a pelear
> con él por el mismo terminal: ambos mandarán heartbeat y configuración de
> dispositivos, y el último en escribir gana. Para desarrollar contra la API real,
> **pide un número de torniquete aparte** (lo autoaprovisiona la API al primer
> arranque, ver `clave_aprovisionamiento.example.json`).

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

:: 2. Los paquetes
python -c "import serial, sqlite3, PIL; print('paquetes ok')"

:: 3. La base responde (se crea sola la primera vez)
python -c "import basedatos; print(basedatos.BDLocal().terminal())"

:: 4. Los dispositivos configurados
python -c "import basedatos; bd=basedatos.BDLocal(); print(bd.lectoras()); print(bd.reles())"

:: 5. Qué hardware ve
python -c "import deteccion_puertos; print(deteccion_puertos.detectar({}))"

:: 6. La app
python main.py
```

El paso 5 debe mostrar los tres aparatos. Si `arduino` sale como `None` pero en
el Administrador de dispositivos aparece, lee §6.3.

---

## 8. Si algo no funciona

| Síntoma | Causa habitual |
| --- | --- |
| `No module named tkinter` | Python instalado sin tcl/tk. Reinstalar marcando esa opción. |
| `numero_torniquete cambió...` al arrancar | Alguien editó `identidad_terminal.json` con un número distinto al ya bloqueado. Ver `identidad_terminal.py`. |
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
