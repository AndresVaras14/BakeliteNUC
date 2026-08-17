# BAKELITE — Control de Acceso

Aplicación de control de acceso para torniquete de **entrada y salida**
(2 lectoras de cédula + 2 relés vía Arduino), en Python.

Reimplementa los contratos de hardware descritos en
[`ESPECIFICACION_HARDWARE.md`](ESPECIFICACION_HARDWARE.md): lee las lectoras por
serie, extrae el RUT, valida el acceso y responde al relé y al semáforo.

---

## Qué hace

- **2 lectoras**: Lectora 1 = **Entrada** (`E`), Lectora 2 = **Salida** (`S`).
- **2 relés** (vía Arduino): al autorizar una entrada dispara `R2*`, al autorizar
  una salida dispara `R1*` (mapeo cruzado, §6.1 de la especificación).
- **Semáforo** (vía Arduino): azul al consultar, verde al autorizar, rojo al
  denegar, amarillo si no hay conexión.
- **Luz azul persistente**: desde que empieza la lectura y **hasta que llega la
  respuesta**, el azul se mantiene tanto en el relé/semáforo (`L1B*`) como en
  pantalla (“VALIDANDO ACCESO…”).
- **Pantalla** estilo BAKELITE: reloj, mensaje de estado y tarjeta “Último
  registro” con nombre, cédula, hora, sentido (ENTRADA/SALIDA) y veredicto.
- **Lista de los últimos 5 registros** debajo de la tarjeta.
- **Solo lee cédula de identidad**: indicado en el encabezado.
- **Pantalla de estado de conexión** (botón 🔌 Estado): muestra si falta
  conectar alguna lectora o el Arduino, con botón **Volver a detectar**. Se abre
  sola al arrancar si falta hardware, y aparece un aviso en la pantalla principal.
- **Botón ⚙ Ajustes** (o tecla `F2`) para **invertir lectoras** y/o **relés**
  cuando quedan al revés, con botones para **probar cada torniquete** y
  **probar las luces**. Los ajustes se guardan en `ajustes.json` y persisten.
- **Círculo de veredicto suavizado** (antialiasing con Pillow; si no está,
  usa un respaldo que funciona igual).

## Estructura

| Archivo | Rol |
|---|---|
| `main.py` | Arranque: detecta puertos, abre hardware, lanza hilos y UI. |
| `config.py` | Toda la configuración (comandos, baudios, tiempos). §14 |
| `deteccion_puertos.py` | Detección y clasificación de puertos serie. §2–3 |
| `arduino.py` | Relés + luces. Escrituras serializadas con lock. §6 |
| `lectora.py` | Hilo de lectura de cada lectora. §8 |
| `rut.py` | Extracción/normalización del RUT (`fnEnmascaraRut`). §8.2 |
| `validador.py` | Valida contra `personas.json` y devuelve el código 0–4. §7 |
| `controlador.py` | Une lectora → validación → luz + relé + pantalla. |
| `interfaz.py` | Interfaz gráfica (Tkinter) + historial + diálogo de ajustes. |
| `ajustes.py` | Inversión de lectoras/relés, persistida en `ajustes.json`. |
| `personas.json` | Base de datos de pruebas (editable). |

## Requisitos

- Python 3.8+
- `pyserial` (para el hardware real):
  ```bash
  pip install -r requirements.txt
  ```
- Tkinter (en Linux puede requerir el paquete del sistema):
  ```bash
  sudo apt install python3-tk
  ```
- El usuario debe estar en el grupo `dialout` para acceder a los puertos (§15):
  ```bash
  sudo usermod -aG dialout $USER   # requiere re-login
  ```

## Ejecutar

```bash
python3 main.py
```

- Si detecta hardware, usa las lectoras y el Arduino reales.
- Si **no** hay hardware, arranca en **modo simulación** (pie de pantalla:
  “Modo simulación”) y puedes probar todo el flujo con el teclado.

Salir de pantalla completa: `Esc`. Alternar: `F11`.

## Modo simulación (teclado)

| Tecla | Acción |
|---|---|
| `1`–`6` | Escanea uno de los 6 carnets en la **Entrada** |
| `Ctrl`+`1`–`6` | Los mismos, en la **Salida** |
| `0` | RUT no registrado → **DENEGADO** |
| `.` | Trama sin formato → **ERROR DE LECTURA** |
| `-` | Fuerza **SIN CONEXIÓN A RED** (amarillo) |

Las teclas `1`–`6` corresponden, en orden, a los RUT de `personas.json`:

| Tecla | RUT | Resultado |
|---|---|---|
| `1` | 4266307-7 | ✅ AUTORIZADO (Laura Sofía Gómez) |
| `2` | 12329308-8 | ✅ AUTORIZADO (Carlos Andrés Rojas) |
| `3` | 9884029-K | ⛔ DENEGADO (credencial vencida) |
| `4` | 17346232-8 | ✅ AUTORIZADO (Diego Martínez Soto) |
| `5` | 20820085-2 | ⛔ DENEGADO (acceso revocado) |
| `6` | 18419773-1 | ✅ AUTORIZADO (Sebastián Torres Vega) |

> Sin hardware, los comandos que se enviarían al Arduino se ven en la consola
> como `-> Arduino [SIM]: R2*`, `L1B*`, `L1G*`, etc. Así se verifica la lógica
> de relés sin tener el torniquete conectado.

## Estado de conexión

Botón **🔌 Estado** (abajo a la derecha). Lista los tres dispositivos que el
sistema necesita —**Lectora 1 (entrada)**, **Lectora 2 (salida)** y **Arduino
(relés + luces)**— con su estado CONECTADO / NO CONECTADO. El botón **Volver a
detectar** vuelve a escanear los puertos y conecta lo que aparezca, sin reiniciar
la app. Si al arrancar falta algo, esta pantalla se abre sola y en la parte
central aparece un aviso “⚠ Falta conectar…”.

## Ajustes: invertir lectoras / relés

Abre el diálogo con el botón **⚙ Ajustes** (abajo a la derecha) o con `F2`:

- **Invertir lectoras (Entrada ↔ Salida):** si la lectora que debía ser la
  entrada quedó asignada como salida (y viceversa). Cambia el sentido mostrado
  y qué relé se dispara.
- **Invertir relés (Entrada ↔ Salida):** si el relé de entrada abre el
  torniquete de salida. Independiente de las lectoras (arregla el cableado).
- **Probar ENTRADA / Probar SALIDA:** dispara el relé correspondiente para que
  veas cuál torniquete abre y decidas si hay que invertir.

El diálogo muestra en vivo el mapeo resultante (Lectora 1 → ENTRADA → `R2*`,
etc.). Todo se guarda en `ajustes.json` y se aplica al instante.

## Editar la base de pruebas

`personas.json` → arreglo `personas`. Cada entrada:

```json
{ "rut": "12345678-9", "nombre": "…", "habilitado": true, "motivo": "", "foto": null }
```

- `habilitado: true` → código 1 (verde + relé). `false` → código 0 (rojo).
- Un RUT que no esté en el archivo → **DENEGADO** (no registrado).

## Notas de producción (pendientes sugeridos)

- Cambiar `Validador` por la consulta real a la BD / WebService (misma firma).
- Clasificar el Arduino por **VID:PID + n.º de serie**, no solo por texto: un
  Arduino clon con CH340 se confunde con una lectora (§2, §16.1).
- Añadir hotplug (detección en caliente) y `HealthMonitor` si se requiere (§4, §10).
- Poner `VALIDACION_DELAY_SIMULADO = 0` cuando la validación real tenga su
  propia latencia.
