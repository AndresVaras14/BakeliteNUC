# El sistema del NUC, por dentro

**Qué es:** la aplicación Python que corre en el NUC del torniquete. Lee cédulas,
decide si la persona pasa, acciona el torniquete y le cuenta a Bakelite todo lo
que ocurre.

**Fecha de este documento:** 2026-08-27
**Alcance:** solo el lado del NUC. Lo que hace la API o la web está en los
contratos, no aquí.

---

## 1. En una frase

Alguien apoya su cédula → se lee el RUT → se pregunta si tiene acceso → se abre
o no el torniquete → queda registrado localmente → se sube a Bakelite. Todo lo
demás de este documento existe para que esa cadena no se corte cuando algo falla.

---

## 2. Los módulos

| Archivo | Qué hace |
| --- | --- |
| `main.py` | Arranque, detección de hardware y cableado de todo lo demás. |
| `supervisor.py` | Lanza `main.py` y lo vuelve a abrir si se cae. **Es lo que se ejecuta en el equipo.** |
| `lectora.py` | Un hilo por lectora, leyendo su puerto serie. |
| `arduino.py` | Envía los comandos de relé y de luces. |
| `deteccion_puertos.py` | Descubre qué hay enchufado y a qué corresponde. |
| `controlador.py` | El orquestador: lectura → validación → luz + relé → registro. |
| `validador.py` | Decide si el RUT tiene acceso. **Hoy contra `personas.json`.** |
| `basedatos.py` | Todo el SQL contra la BD local. |
| `registros.py` | Cola en JSON de las marcas pendientes de subir. |
| `sincronizador.py` | Sube marcas, vigila la salud de la API, informa cortes, sincroniza el nombre. |
| `presencia.py` | Heartbeat: avisa que este proceso está vivo. |
| `dispositivos.py` | Sincroniza lectoras y relés con Bakelite. |
| `ajustes.py` | Configuración operativa (qué lectora es entrada, ubicación). |
| `interfaz.py` | La pantalla: modo torniquete y modo PC. |
| `widgets.py` | Botones, píldoras y paneles redondeados. |
| `depurador.py` | Registro paso a paso del modo debugger. |
| `rut.py` | Extracción y normalización del RUT chileno. |
| `config.py` | Todos los parámetros en un solo lugar. |

### Hilos que corren a la vez

`Sincronizador` · `Heartbeat` · `Dispositivos` · una `LectoraN` por lectora ·
vigilancia de puertos · un hilo `AccesoN` por cada validación en curso · la
interfaz en el hilo principal.

Ninguno espera a otro por red. **El único punto donde se serializan es la BD
local**, que tiene un lock porque pyodbc no permite compartir una conexión entre
hilos.

---

## 3. El flujo de un acceso

1. **Llega la trama** de la lectora (`?RUN=...`).
2. **¿Es la misma cédula apoyada?** Si entre lecturas pasó menos de 1,5 s, se
   ignora: la cédula sigue sobre el lector.
3. **¿Cooldown?** 2 s tras procesar. Una cédula **distinta** se salta el
   cooldown: en un torniquete la gente pasa una detrás de otra.
4. **¿La lectora está ocupada?** Si tiene una consulta en curso, no se acepta
   otra en ella. Tope de seguridad de 30 s por si algo se cuelga.
5. Se marca la lectora como ocupada y **el trámite sale del hilo de la lectora**,
   que sigue leyendo.
6. **Se abre la marca** en `dbo.Marcas` (antes de preguntar nada: la cédula ya
   pasó).
7. **Luz azul + "VALIDANDO"** y se consulta al validador, con tope de **7 s**.
8. **Se aplica el resultado**: relé primero, luz después (0,15 s de diferencia,
   para que la persona vea verde con el paso ya liberado).
9. Se cierra la consulta en la BD, se encola para Bakelite y se pinta en pantalla.

### Códigos de resultado

| Código | Significado | Luz | Relé |
| ---: | --- | --- | --- |
| 0 | No habilitado | roja | no |
| 1 | **Habilitado** | verde | **sí** |
| 2 | Rechazo especial | roja | no |
| 3 | Lectura inválida | roja | no |
| 4 | Sin conexión a red | amarilla | no |
| 5 | **Sin respuesta en 7 s — vuelva a intentar** | amarilla | no |

---

## 4. Lo que envía y recibe de Bakelite

Base: `https://bakeliteapi.sopytec.cl` · todos anónimos, sin JWT.

### 4.1 Resumen

| Endpoint | Método | Cada | Para qué |
| --- | --- | ---: | --- |
| `/api/terminal/events` | POST | al haber marcas, reintento cada 10 s | Subir cada acceso |
| `/api/terminal/health` | GET | 10 s | ¿Está viva la API? |
| `/api/terminal/incidents` | POST | al recuperarse | Informar un corte ya superado |
| `/api/terminal/{id}` | GET | al iniciar | ¿Existe y está activo el terminal? |
| `/api/terminal/{id}/heartbeat` | POST | 10 s | Avisar que este proceso vive |
| `/api/terminal/{id}/dispositivos/sincronizar` | POST | 10 s + ante cambios | Lectoras y relés |
| `/api/terminal/{id}/nombre-terminal/comparar` | POST | 60 s | ¿Cambió el nombre? |
| `/api/terminal/{id}/nombre-terminal/hacia-nuc` | GET | al detectar cambio | Adoptar el nombre |
| `/api/terminal/{id}/nombre-terminal/desde-nuc` | PUT | al renombrar aquí | Subir el nombre |

### 4.2 Marcas — `POST /api/terminal/events`

**Envía:** `idEvento` (UUID que no cambia entre reintentos), `idTerminal`,
`rut`, `evento` (ENTRADA/SALIDA), `fechaHora`, `autorizado`, y según el caso
`nombre` o `motivoRechazo`.

**Recibe:** `idMarca` y `estado` (`REGISTRADO` o `DUPLICADO`).

| Respuesta | Qué hace el NUC |
| --- | --- |
| `201` / `200` | Marca entregada (`subido_api = 1`) |
| `400` | Fallo definitivo (`subido_api = -1`), **no se reintenta** |
| `429`, `5xx`, timeout | Sigue pendiente, espera incremental hasta 60 s |

El payload se guarda **antes** de enviarse y se manda tal cual en cada
reintento: eso es lo que hace que la idempotencia funcione.

### 4.3 Salud — `GET /api/terminal/health`

**Envía:** nada. **Recibe:** `estado`, `baseDatos`, `version`.

Hay conexión **solo** si es `200` **y** `baseDatos = "OK"`. Un `200` con la BD
caída cuenta como sin conexión: la API responde pero no podría guardar la marca.
Un `404` significa que la API no está publicada, no que falte una ruta.

### 4.4 Incidentes — `POST /api/terminal/incidents`

Cortes que **vio el NUC** ("no pude alcanzar la API"). Se envían cuando el
servicio ya volvió. Llevan `idIncidente` (UUID creado una sola vez),
`fechaDeteccion`, `fechaRecuperacion`, `duracionSegundos`, `intentosFallidos`.

Un corte de menos de 1 segundo no se informa: el contrato lo rechazaría.

> No confundir con los incidentes de **ausencia**, que detecta la API cuando el
> NUC deja de latir. Son del otro observador y hoy no se consumen.

### 4.5 Presencia — `POST /api/terminal/{id}/heartbeat`

**Envía:** nada (cuerpo vacío). **Recibe:** `estado`, `heartbeatCadaSegundos`,
`idIncidenteRecuperado`.

El intervalo lo manda la API. `409` (terminal inactivo) **detiene los latidos
para siempre** y avisa al operador; `404` espacia a 60 s.

### 4.6 Dispositivos — `POST .../dispositivos/sincronizar`

**Envía**, por cada lectora y relé: `numero`, `sentido`, `descripcion`, activo,
`configFecha`, `configPor`, y el estado observado (`conectada`, `puerto`,
`ultimaLectura`, `ultimoDisparo`, `ultimoError`). Más `arduino` y un `idCambio`.

**Recibe:** `aplicar` (lo que en Bakelite es más reciente y hay que adoptar),
`resultados` por dispositivo, y `sincronizarCadaSegundos`.

Se envía al iniciar, **ante cualquier cambio** y cada 10 s. Los relés informan
`conectado` derivado del Arduino: no tienen puerto propio.

### 4.7 Nombre del terminal

Comparación cada 60 s (no escribe nada). Si el nombre de la API es más nuevo se
adopta **conservando su fecha**; si el local es más nuevo, se sube. Empate exacto
→ gana la API.

---

## 5. La base de datos local

SQL Server, base `BakeliteTorniquete`, vía pyodbc.

| Tabla | Qué guarda |
| --- | --- |
| `Terminales` | Identidad, nombre sincronizado, estado del Arduino |
| `Lectoras` / `Reles` | Configuración (sentido) y estado observado |
| `Marcas` | Cada acceso, con su estado de envío |
| `Trabajadores` | Quién es cada RUT, según respondió la API externa |
| `ConsultasApiExterna` | Qué se preguntó y qué respondió, con duración |
| `EnviosBakelite` | Cada intento de subir una marca |
| `IncidentesConexion` | Cortes vistos por el NUC |
| `EstadoServicios` | Última conexión conocida de cada servicio |
| `Errores` | **El registro de problemas y hechos relevantes** |
| `Versiones` | Versión de la aplicación |

**Si SQL Server no está disponible, la app sigue funcionando.** Las marcas quedan
en `bakelite_nuc.db` y se reconstruyen al reconectar.

---

## 6. Qué se registra y qué no

Hay **cinco destinos** distintos y conviene no confundirlos.

### 6.1 Los cinco destinos

| Destino | Qué recibe | Rota |
| --- | --- | --- |
| `logs/app.log` | Todo desde nivel INFO | 1 MB × 4 |
| `logs/errores.log` | Solo ERROR y CRITICO | 1 MB × 6 |
| `logs/debugger.log` | El registro paso a paso del modo debugger | 2 MB × 2 |
| `bakelite_nuc.db` → `BitacoraAplicacion` | Logging y acciones estructuradas de todos los módulos | configurable; 0 = sin borrado |
| `dbo.Errores` | **Lo que importa que quede en la base** | no rota |

La consola muestra lo mismo que `app.log`.

### 6.2 Lo que SÍ queda en `dbo.Errores`

Esta tabla no es solo de errores: guarda también hechos buenos que importan.

| Hecho | Nivel | Origen |
| --- | --- | --- |
| **Conexión con Bakelite recuperada** (con duración del corte) | `INFO` | `api` |
| **Conexión con la API externa recuperada** | `INFO` | `api_externa` |
| Supervisor iniciado | `INFO` | `supervisor` |
| Cambio de configuración de dispositivos adoptado desde la web | `INFO` | `dispositivos` |
| Conflicto de configuración resuelto | `WARN` | `dispositivos` |
| Sin conexión con Bakelite (abre incidente) | `ERROR` | `api` |
| Sin conexión con la API externa | `ERROR` | `api_externa` |
| Marca rechazada con `400` | `ERROR` | `api` |
| Una lectora quedó ocupada más de 30 s y se liberó por la fuerza | `ERROR` | `controlador` |
| Bakelite rechazó la configuración de dispositivos | `ERROR` | `dispositivos` |
| **La aplicación se cayó** (con código y duración) | `ERROR` | `supervisor` |
| El nombre fue rechazado (`400`) | `ERROR` | `config` |
| **El `idTerminal` no existe en Bakelite** | `CRITICO` | `config` |
| **El terminal está INACTIVO en Bakelite** | `CRITICO` | `config` |
| **Crash-loop:** varias caídas seguidas | `CRITICO` | `supervisor` |

Los cuatro niveles son `INFO`, `WARN`, `ERROR` y `CRITICO`. Cada fila lleva
`IdTerminal`, origen, mensaje, detalle y fecha; las de dispositivos incluyen el
**`idCambio`**, que es lo que permite cruzarlas con el registro de la API.

### 6.3 Lo que NO queda en `dbo.Errores` — y por qué

| No se registra | Motivo |
| --- | --- |
| **Cada sondeo de salud** (cada 10 s) | 8.640 filas diarias diciendo "todo bien" |
| **Cada heartbeat** | Lo mismo; la API guarda el estado consolidado |
| **Cada sincronización de dispositivos sin cambios** | Solo se registran los **cambios** |
| **Cada comparación del nombre** | No escribe nada en ningún lado |
| **Los accesos normales** | Van a `dbo.Marcas`, que es su lugar |
| **Las consultas a la API externa** | Van a `dbo.ConsultasApiExterna`, con su duración |
| **Cada intento de subir una marca** | Va a `dbo.EnviosBakelite` |
| **Lecturas ignoradas** (cédula apoyada, cooldown) | Ruido: son cientos por acceso |
| **Fallos de red repetidos** | Se registra el **primero** y luego uno de cada 30 |
| Un 404 aislado del heartbeat | Hace falta que se repita 3 veces: podría ser la API caída, no un error de configuración |

El criterio es: **a SQL Server va lo operacional consolidado**; SQLite conserva
el detalle estructurado local y los archivos permiten una revisión rápida.

### 6.4 El modo debugger

Se activa desde **Ajustes → Diagnóstico** y parte la pantalla en dos. Registra
tres flujos:

- `→` **lo que se hace**: renombrar, cambiar un sentido, probar un relé, consultar la API
- `←` **lo que se recibe**: qué RUT leyó cada lectora, la respuesta de la API **con su duración en ms**
- `·` **el detalle**: todo lo que la app escribe con `logging`

Se guarda en `logs/debugger.log`, **se conserva al salir y se carga al entrar**:
sirve para revisar lo que pasó antes de abrirlo.

---

## 7. Qué pasa cuando algo falla

| Falla | Qué ocurre |
| --- | --- |
| **La API no responde** | Las marcas se encolan; el acceso sigue funcionando |
| **La BD local no responde** | Las marcas quedan en `bakelite_nuc.db` y se reconstruyen al volver |
| **La consulta tarda más de 7 s** | Se abandona: "SIN RESPUESTA — VUELVA A INTENTAR" |
| **Una lectora se desenchufa** | Se detecta en ≤10 s, se informa a Bakelite y la pantalla lo muestra |
| **Se vuelve a enchufar** | Recupera su número (ancla por zócalo USB) y se limpia el error |
| **El Arduino se desenchufa** | Se informa; los relés pasan a `conectado = false` |
| **Algo queda colgado** | La lectora se libera a los 30 s y vuelve a aceptar cédulas |
| **La app se cae** | El supervisor la relanza y **registra la caída en la BD** |
| **Se cae muchas veces seguidas** | Espera 60 s y lo marca como `CRITICO` |

---

## 8. La pantalla

**Modo torniquete** (el de arranque): luz grande con degradado a la derecha —40%
del ancho—, y a la izquierda el veredicto, quién pasó, el último acceso y el
estado de las dos APIs. Las letras escalan con el tamaño del monitor.

**Modo PC**: la vista completa con historial, aforo, semáforo de referencia y
acceso a los ajustes. Se cambia con el switch de arriba a la derecha.

Cuatro accesos en el pie: **Estado** (hardware), **Terminal** (nombre y
ubicación), **Ajustes** (lectoras y relés) y **Pruebas** (torniquetes y luces).

---

## 9. Lo que todavía no está

| Pendiente | Estado |
| --- | --- |
| **La validación real de personas** | Hoy contra `personas.json`, 6 RUT de prueba. Es lo único simulado del flujo. |
| Bajar los incidentes de ausencia que detecta la API | Opcional en su contrato; no implementado |
| Honrar la baja lógica de un dispositivo | Se guarda `Activa`, pero **la app la ignora**: una lectora dada de baja sigue leyendo |
| Identidad del operador | `ConfigPor` va fijo como `"operador"`: la app no tiene login |

---

## 10. Los contratos

| Documento | Cubre |
| --- | --- |
| `ESPECIFICACION_HARDWARE.md` | Comandos, baudios, códigos: el contrato con el hardware |
| `CONTRATO_INTEGRACION_TORNIQUETE.md` | Envío de marcas |
| `CONTRATO_ENDPOINTS_PENDIENTES.md` | Salud, incidentes y datos del terminal |
| `CONTRATO_SINCRONIZACION_NOMBRE_TERMINAL.md` | Nombre del terminal |
| `CONTRATO_DISPONIBILIDAD_SOFTWARE_ESCRITORIO.md` | Presencia del proceso |
| `CONTRATO_DISPOSITIVOS_TERMINAL.md` | Lectoras y relés, + pendientes de toda la integración |
