# Contrato de presencia de la app Python

**Versión:** 1.0

**Fecha:** 2026-08-26

**Componentes:** app Python, Bakelite API y Bakelite Web

**Base URL de producción:** `https://bakeliteapi.sopytec.cl`

## 1. Objetivo

Este contrato permite que Bakelite Web muestre si la **aplicación Python del
terminal** está comunicándose con la API.

`EN_LINEA` significa que la API recibió recientemente un heartbeat del proceso
Python. No garantiza por sí solo que el torniquete, lector u otro periférico
esté funcionando. El estado de esos dispositivos debe tener un contrato
separado.

La API es la única autoridad del estado. Python informa presencia, la API
calcula el estado y la web solamente lo consulta.

```text
App Python -- POST heartbeat cada 10 s --> API/BD
                                             |
                                      monitor cada 1 s
                                             |
Bakelite Web <-- GET estado cada 2 s --------+
```

## 2. Reglas temporales

| Regla | Valor |
| --- | ---: |
| Intervalo esperado del heartbeat de Python | 10 segundos |
| Declaración de desconexión | más de 30 segundos sin heartbeat |
| Revisión interna de la API | 1 segundo |
| Actualización de Bakelite Web | 2 segundos |
| Reloj autoritativo | reloj de la API |

La API debe entregar los intervalos en sus respuestas. Los clientes no deben
inventar fechas de heartbeat ni decidir por su cuenta cuándo el terminal está
en línea.

La latencia máxima normal para que la web muestre una caída es de
aproximadamente 33 segundos: 30 segundos de tolerancia, hasta 1 segundo del
monitor y hasta 2 segundos del sondeo web.

## 3. Estados

### Estados autoritativos de la API

| Estado | Significado |
| --- | --- |
| `SIN_REGISTRO` | El terminal existe, pero nunca recibió un heartbeat. |
| `EN_LINEA` | El último heartbeat está dentro de la tolerancia. |
| `SIN_CONEXION` | Pasaron más de 30 segundos sin heartbeat. |
| `INACTIVO` | El terminal fue desactivado administrativamente. |

### Estado local adicional de la web

| Estado | Significado |
| --- | --- |
| `DESCONOCIDO_API` | La web no pudo consultar a la API y no puede afirmar si Python sigue conectado. |

`DESCONOCIDO_API` no es una respuesta del backend. Es un estado de presentación
obligatorio para evitar que la web conserve indefinidamente un “En línea”
antiguo cuando la propia API o la red del navegador no están disponibles.

## 4. Endpoint de escritura: Python informa presencia

```http
POST /api/terminal/{idTerminal}/heartbeat
Content-Length: 0
```

- No requiere JWT.
- No lleva body.
- Debe enviarse inmediatamente al iniciar Python y luego cada 10 segundos.
- La fecha registrada siempre es la fecha del servidor.
- Una petición confirma presencia; una consulta `GET` nunca renueva el estado.
- Los heartbeats no se guardan uno a uno en `LogsSistema`; se actualiza el
  estado consolidado del terminal.

### Respuesta `200 OK`

```json
{
  "idTerminal": 1,
  "estado": "EN_LINEA",
  "fechaHoraServidor": "2026-08-26T10:20:30-04:00",
  "heartbeatCadaSegundos": 10,
  "sinConexionTrasSegundos": 30,
  "idIncidenteRecuperado": null
}
```

Cuando este heartbeat recupera una caída, `idIncidenteRecuperado` contiene el
identificador numérico del incidente que la API acaba de cerrar.

### Códigos y conducta de Python

| HTTP | Significado | Conducta obligatoria |
| --- | --- | --- |
| `200` | Heartbeat aceptado. | Programar el próximo ciclo. |
| `404` | El terminal no existe. | Registrar error de configuración; no reintentar agresivamente. |
| `409` | El terminal está inactivo. | Detener heartbeats y avisar al operador. |
| `429` | Se excedió el límite. | Respetar `Retry-After` si existe y reintentar después. |
| `5xx`, timeout o error de red | Fallo temporal. | Mantener la aplicación operativa y reintentar en el ciclo siguiente. |

Solo puede existir una petición de heartbeat en vuelo. El timeout recomendado
es de 5 segundos. Un error nunca debe producir ciclos sin espera.

### Pseudocódigo Python

```python
while app_running:
    started = monotonic()
    try:
        response = post(f"/api/terminal/{terminal_id}/heartbeat", timeout=5)
        handle_status(response.status_code)
    except TemporaryNetworkError:
        log_warning_once_per_failure_window()

    wait(max(0, heartbeat_interval - (monotonic() - started)))
```

El ciclo debe usar un reloj monotónico para la espera. Si una llamada tarda,
no se deben acumular peticiones atrasadas.

## 5. Endpoint de lectura: diagnóstico directo

```http
GET /api/terminal/{idTerminal}/availability
```

- No requiere JWT.
- No modifica el último heartbeat.
- Devuelve `404` si el terminal no existe.
- Responde con `Cache-Control: no-store`.

### Respuesta `200 OK`

```json
{
  "idTerminal": 1,
  "estado": "SIN_CONEXION",
  "fechaHoraServidor": "2026-08-26T10:21:12-04:00",
  "ultimaConexion": "2026-08-26T10:20:30-04:00",
  "fechaDeteccionCaida": "2026-08-26T10:21:01-04:00",
  "segundosSinConexion": 42,
  "heartbeatCadaSegundos": 10,
  "sinConexionTrasSegundos": 30
}
```

Reglas de nulabilidad:

- `ultimaConexion` es `null` en `SIN_REGISTRO`.
- `fechaDeteccionCaida` solo tiene valor en `SIN_CONEXION`.
- `segundosSinConexion` solo es mayor que cero en `SIN_CONEXION`.

Este endpoint es apropiado para diagnóstico y para clientes que solo necesitan
el estado. La web actual obtiene el mismo hecho operativo dentro de su snapshot
autenticado, descrito a continuación.

## 6. Lectura de Bakelite Web

```http
GET /api/access/live
Authorization: Bearer {accessToken}
```

- Requiere una sesión web válida.
- Responde con `Cache-Control: no-store`.
- La web debe consultarlo al cargar y cada 2 segundos.
- No debe iniciarse una consulta nueva mientras la anterior siga en vuelo.
- Un `401` permite un único intento de renovación de sesión.

### Fragmento relevante de la respuesta actual

```json
{
  "terminal": {
    "id": 1,
    "name": "Torniquete Principal",
    "online": true,
    "lastSignalSeconds": 3,
    "lastSignalAt": "2026-08-26T10:20:30-04:00"
  },
  "deniedToday": 0,
  "latest": null,
  "history": []
}
```

`terminal.online` lo calcula la API usando el estado persistido y el límite de
30 segundos. La web no debe cambiarlo a `true` basándose en marcas recientes ni
en que `GET /api/access/live` haya respondido correctamente.

### Presentación obligatoria

| Condición | Texto sugerido | Color |
| --- | --- | --- |
| `online === true` | `App Python conectada` | Verde |
| `online === false` y `lastSignalAt != null` | `App Python desconectada` | Rojo |
| `online === false` y `lastSignalAt == null` y `id > 0` | `Esperando primera conexión` | Gris/ámbar |
| `id == 0` | `No hay terminal activo` | Gris |
| Falló el sondeo web | `Estado no disponible` | Gris/ámbar |

Ante un fallo temporal del sondeo, la web puede conservar el último dato como
referencia, pero debe marcarlo como no confirmado. No debe mostrarlo como estado
actual después de **6 segundos** sin una respuesta válida de la API. Al
recuperarse la consulta, reemplaza inmediatamente el estado local con la nueva
respuesta.

Para evitar cambios visuales falsos, el contador “última señal hace N segundos”
puede avanzar localmente, pero jamás puede convertir por sí solo el estado a
conectado o desconectado.

## 7. Detección, incidente y recuperación

No se crea una caída antes del primer heartbeat. Al superar 30 segundos sin
comunicación, el monitor de la API realiza atómicamente lo siguiente:

1. Cambia el estado a `SIN_CONEXION`.
2. Conserva la última comunicación conocida.
3. Registra el momento de detección.
4. Crea un único incidente abierto con UUID.

Mientras Python siga desconectado no se crean incidentes adicionales. El
primer heartbeat de recuperación:

1. Cambia el estado a `EN_LINEA`.
2. Cierra el incidente abierto.
3. Guarda la fecha de recuperación.
4. Calcula la duración desde el último heartbeat.

## 8. Sincronización opcional de caídas hacia Python

Si la base de datos local de Python debe conservar el historial central:

```http
GET /api/terminal/{idTerminal}/availability/incidents?afterId=0&limit=100
```

### Respuesta `200 OK`

```json
{
  "items": [
    {
      "idRegistro": 7,
      "idIncidente": "6b4626102af94cc3a47f066f31f052eb",
      "idTerminal": 1,
      "fechaUltimaComunicacion": "2026-08-26T10:20:30-04:00",
      "fechaDeteccion": "2026-08-26T10:21:01-04:00",
      "fechaRecuperacion": "2026-08-26T10:22:14-04:00",
      "duracionSegundos": 104
    }
  ],
  "siguienteAfterId": 7,
  "hayMas": false
}
```

| Parámetro | Regla |
| --- | --- |
| `afterId` | Cursor exclusivo; usar `0` en la primera consulta. |
| `limit` | Entre 1 y 100. |

Solo se entregan incidentes cerrados. Python debe insertar por
`idIncidente` único y avanzar `siguienteAfterId` en la misma transacción. Si
`hayMas` es `true`, consulta inmediatamente la página siguiente.

## 9. Responsabilidades por componente

### App Python

1. Conocer su `idTerminal` por configuración.
2. Enviar un heartbeat al iniciar y luego cada 10 segundos.
3. No enviar su propia fecha ni decidir el estado global.
4. Manejar los códigos HTTP sin bucles agresivos.
5. Opcionalmente sincronizar incidentes cerrados por cursor.

### Bakelite API

1. Validar que el terminal exista y esté activo.
2. Usar siempre la hora del servidor.
3. Actualizar el estado de manera idempotente y transaccional.
4. Declarar la caída al superar el umbral.
5. Evitar más de un incidente abierto por terminal.
6. Entregar estado sin caché tanto a diagnóstico como a la web.

### Bakelite Web

1. Consultar el snapshot inmediatamente y cada 2 segundos.
2. Mostrar el estado en todas las vistas mediante el indicador global.
3. Diferenciar desconexión de Python, primera conexión pendiente y API no
   disponible.
4. No conservar indefinidamente como vigente una respuesta antigua.
5. Cancelar el sondeo al cerrar sesión o desmontar la aplicación.

## 10. Estado de implementación al 2026-08-26

| Pieza | Estado |
| --- | --- |
| `POST /api/terminal/{id}/heartbeat` | Implementado en Bakelite API |
| Monitor y persistencia de estado/caídas | Implementado en Bakelite API |
| `GET /api/terminal/{id}/availability` | Implementado en Bakelite API |
| `GET /availability/incidents` | Implementado en Bakelite API |
| Estado dentro de `GET /api/access/live` | Implementado en Bakelite API |
| Sondeo web cada 2 segundos e indicador global | Implementado en Bakelite Web |
| Estado visual `DESCONOCIDO_API` luego de 6 segundos | Pendiente de adecuación web |
| Envío periódico desde la app Python | Debe implementarse/verificarse en el repositorio Python |

## 11. Criterios de aceptación de punta a punta

1. Con Python iniciado, la web muestra `App Python conectada` luego del primer
   heartbeat y del siguiente sondeo web.
2. Al detener Python, la web cambia a desconectada en no más de 33 segundos.
3. Al reiniciar Python, la web vuelve a conectada en no más de 2 segundos
   después del heartbeat aceptado.
4. Refrescar o cambiar de vista no altera el estado calculado.
5. Si la API deja de responder, la web muestra `Estado no disponible` en no más
   de 6 segundos; no muestra falsamente que Python está desconectado.
6. Una caída genera un solo incidente y la recuperación lo cierra.
7. Repetir la sincronización de incidentes no duplica registros en Python.
