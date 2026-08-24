# Contrato de sincronización del nombre del terminal

**Destinatario:** equipo de BakeliteApi y equipo del software Python del NUC.
**Vigencia:** 2026-08-24.
**Estado:** implementado y probado en BakeliteApi; integración del ciclo
automático pendiente de verificar en Python.
**Reemplaza:** el punto 6 y el criterio de aceptación 7 de
`CONTRATO_ENDPOINTS_PENDIENTES.md`, que declaraban a Bakelite como única
fuente del nombre.

---

## 1. Problema que resuelve

El nombre de un terminal se puede cambiar desde dos lugares:

- en el **NUC**, por el operador, desde la pantalla de ajustes de la aplicación;
- en la **web de Bakelite**, por un administrador.

Hasta ahora cada lado se consideraba dueño del dato, así que un cambio en un
extremo quedaba invisible en el otro y no había forma de saber cuál de los dos
nombres era el vigente.

La regla que se adopta es **el último cambio gana** (*last write wins*): ambos
lados guardan la fecha y hora exactas en que se cambió el nombre, y prevalece
el nombre cuya fecha sea más reciente. Ninguno de los dos es autoritativo por
sí mismo: lo autoritativo es la hora del cambio.

---

## 2. Estructura de la tabla

La tabla `dbo.Terminales` debe tener **los mismos campos de sincronización del
nombre en ambos lados**, aunque sus columnas generales no sean idénticas. La
estructura del lado del NUC, confirmada en la base local `BakeliteTorniquete`,
es:

```sql
CREATE TABLE dbo.Terminales (
    IdTerminal          INT               NOT NULL
        CONSTRAINT PK_Terminales PRIMARY KEY,
    Nombre              NVARCHAR(150)     NOT NULL,
    Ubicacion           NVARCHAR(200)     NULL,
    Activo              BIT               NOT NULL
        CONSTRAINT DF_Terminales_Activo DEFAULT (1),
    FechaCreacion       DATETIME2(0)      NOT NULL
        CONSTRAINT DF_Terminales_FCrea DEFAULT (SYSDATETIME()),
    FechaModificacion   DATETIME2(0)      NULL,
    ModificadoPor       NVARCHAR(100)     NULL,

    /* --- Sincronización del nombre (last write wins) --- */
    NombreFecha         DATETIMEOFFSET(0) NOT NULL
        CONSTRAINT DF_Terminales_NombreFecha DEFAULT (SYSDATETIMEOFFSET()),
    NombreOrigen        VARCHAR(10)       NOT NULL
        CONSTRAINT DF_Terminales_NombreOrigen DEFAULT ('LOCAL'),
    NombrePor           NVARCHAR(100)     NULL,
    NombreSincronizado  BIT               NOT NULL
        CONSTRAINT DF_Terminales_NombreSync DEFAULT (1),

    CONSTRAINT CK_Terminales_Nombre CHECK (LEN(LTRIM(RTRIM(Nombre))) > 0),
    CONSTRAINT CK_Terminales_NombreOrigen CHECK (NombreOrigen IN ('LOCAL','API'))
);
```

| Columna | Tipo | Significado |
| --- | --- | --- |
| `IdTerminal` | `INT` | Mismo identificador en ambos lados. En este equipo es `1`. |
| `Nombre` | `NVARCHAR(150)` | Nombre visible del terminal. Nunca vacío. |
| `NombreFecha` | `DATETIMEOFFSET(0)` | Momento exacto en que se cambió **el nombre**. Es el único criterio de desempate. Con zona horaria, precisión de un segundo. |
| `NombreOrigen` | `VARCHAR(10)` | Dónde se originó el cambio: `LOCAL` (NUC) o `API` (web). Sirve para auditar y para desempatar. |
| `NombrePor` | `NVARCHAR(100)` | Usuario que hizo el cambio. Informativo. |
| `NombreSincronizado` | `BIT` | Solo existe en el NUC: `0` = el cambio local todavía no se subió a la API. No se agrega en Bakelite. |
| `FechaModificacion` / `ModificadoPor` | | Cambios generales de la fila (ubicación, activo). **No** participan en la sincronización del nombre. |

`NombreFecha` es deliberadamente distinta de `FechaModificacion`: cambiar la
ubicación no debe hacer que un nombre viejo gane la comparación.

### 2.1 Migraciones

El NUC ya tiene las columnas de sincronización mostradas en la estructura
anterior. Para una instalación local anterior, puede usar estas sentencias
idempotentes:

```sql
IF COL_LENGTH(N'dbo.Terminales', N'NombreFecha') IS NULL
    ALTER TABLE dbo.Terminales ADD NombreFecha DATETIMEOFFSET(0) NULL;
GO

IF COL_LENGTH(N'dbo.Terminales', N'NombreOrigen') IS NULL
    ALTER TABLE dbo.Terminales ADD NombreOrigen VARCHAR(10) NULL;
GO

IF COL_LENGTH(N'dbo.Terminales', N'NombrePor') IS NULL
    ALTER TABLE dbo.Terminales ADD NombrePor NVARCHAR(100) NULL;
GO

IF COL_LENGTH(N'dbo.Terminales', N'NombreSincronizado') IS NULL
    ALTER TABLE dbo.Terminales ADD NombreSincronizado BIT NULL;
GO

/* Las filas anteriores heredan la fecha que ya tenían. Si nunca se editaron,
   se usa la de creación: cualquier cambio posterior será más reciente. */
UPDATE dbo.Terminales
   SET NombreFecha        = COALESCE(NombreFecha,
                                     TODATETIMEOFFSET(COALESCE(FechaModificacion,
                                                               FechaCreacion),
                                                      DATEPART(TZOFFSET, SYSDATETIMEOFFSET()))),
       NombreOrigen       = COALESCE(NombreOrigen, 'LOCAL'),
       NombreSincronizado = COALESCE(NombreSincronizado, 0)
 WHERE NombreFecha IS NULL OR NombreOrigen IS NULL OR NombreSincronizado IS NULL;
GO
```

En BakeliteApi no se debe ejecutar literalmente ese bloque porque la tabla del
servidor utiliza `CreadoEn` y `ModificadoEn`. El servidor usa la migración
`012_TerminalNameSynchronization.sql`, que:

- amplía `Nombre` de 100 a 150 caracteres;
- agrega `NombreFecha`, `NombreOrigen` y `NombrePor`;
- inicializa las filas existentes con `ModificadoEn` o `CreadoEn`;
- usa `NombreOrigen = 'API'` para los datos existentes;
- no agrega `NombreSincronizado`.

### 2.2 Requisito de reloj

La comparación depende de que los dos relojes estén en hora. Ambos servidores
deben mantener sincronía NTP. Todas las fechas viajan en **ISO 8601 con offset
explícito** (`2026-08-24T10:15:30-04:00`); nunca sin zona horaria. Cada lado
compara en UTC, no en hora local.

---

## 3. Endpoints

Base de producción:

```text
https://bakeliteapi.sopytec.cl
```

Anónimos, con el limitador de solicitudes del terminal y `Cache-Control: no-store`,
igual que el resto de endpoints del terminal.

| Método y ruta | Uso |
| --- | --- |
| `POST /api/terminal/{idTerminal}/nombre-terminal/comparar` | Comparar ambos nombres y decidir cuál está más actualizado. No modifica nada. |
| `GET /api/terminal/{idTerminal}/nombre-terminal/hacia-nuc` | Obtener el nombre de la API para que el NUC actualice el suyo. |
| `PUT /api/terminal/{idTerminal}/nombre-terminal/desde-nuc` | Subir el nombre del NUC para que la API actualice el suyo. |

---

### 3.1 Comparar — quién está más actualizado

El NUC envía lo que tiene; la API responde qué debe hacerse. **Esta llamada no
escribe nada en ninguno de los dos lados.**

```http
POST https://bakeliteapi.sopytec.cl/api/terminal/1/nombre-terminal/comparar
Content-Type: application/json
```

```json
{
  "nombre": "Torniquete Portería Norte",
  "nombreFecha": "2026-08-24T10:15:30-04:00",
  "nombreOrigen": "LOCAL"
}
```

| Campo | Tipo | Obligatorio | Regla |
| --- | --- | --- | --- |
| `nombre` | string | Sí | 1 a 150 caracteres, sin espacios al inicio ni al final. |
| `nombreFecha` | ISO 8601 | Sí | Debe incluir `Z` u offset. Se rechaza con `400` si está más de 5 minutos en el futuro respecto del reloj del servidor. |
| `nombreOrigen` | string | No | `LOCAL` o `API`. El NUC envía siempre `LOCAL`. |

#### HTTP 200

```json
{
  "idTerminal": 1,
  "veredicto": "ACTUALIZAR_API",
  "iguales": false,
  "local":  { "nombre": "Torniquete Portería Norte", "nombreFecha": "2026-08-24T10:15:30-04:00", "nombreOrigen": "LOCAL" },
  "api":    { "nombre": "Torniquete Principal",      "nombreFecha": "2026-08-20T09:02:11-04:00", "nombreOrigen": "API" },
  "activo": true
}
```

| `veredicto` | Significado | Qué hace el NUC |
| --- | --- | --- |
| `IGUALES` | Mismo nombre en ambos lados. | Nada. Marca `NombreSincronizado = 1`. |
| `ACTUALIZAR_LOCAL` | El nombre de la API es más reciente. | Llama a `GET .../hacia-nuc` y adopta ese nombre. |
| `ACTUALIZAR_API` | El nombre del NUC es más reciente. | Llama a `PUT .../desde-nuc` y sube el suyo. |

Reglas de decisión, en orden:

1. Si los dos nombres son idénticos carácter a carácter → `IGUALES`. No importa
   la fecha: no hay nada que propagar. Si aun así las fechas difieren, cada lado
   conserva la suya; no se fuerza ninguna escritura.
2. Si difieren, gana el `nombreFecha` **mayor** en UTC.
3. Si las fechas son exactamente iguales al segundo y los nombres difieren, gana
   **la API** (`ACTUALIZAR_LOCAL`). Es un desempate arbitrario pero determinista:
   ambos lados llegan a la misma conclusión sin negociar.

Si el terminal no existe → `HTTP 404`. Un terminal inactivo se compara igual y
se informa con `"activo": false`.

---

### 3.2 Actualizar el NUC — leer el nombre de la API

```http
GET https://bakeliteapi.sopytec.cl/api/terminal/1/nombre-terminal/hacia-nuc
```

Sin parámetros ni cuerpo.

#### HTTP 200

```json
{
  "idTerminal": 1,
  "nombre": "Torniquete Principal",
  "nombreFecha": "2026-08-20T09:02:11-04:00",
  "nombreOrigen": "API",
  "nombrePor": "admin.bakelite",
  "activo": true
}
```

El NUC escribe ese nombre en su tabla local **conservando la `nombreFecha`
recibida**, no la hora en que lo aplicó. Si guardara la hora de aplicación, su
copia parecería siempre más nueva que la de la API y el cambio rebotaría de
vuelta en la comparación siguiente.

```sql
UPDATE dbo.Terminales
   SET Nombre = @nombre,
       NombreFecha = @nombreFecha,      -- la que devolvió la API, tal cual
       NombreOrigen = 'API',
       NombrePor = @nombrePor,
       NombreSincronizado = 1
 WHERE IdTerminal = @idTerminal
   AND NombreFecha < @nombreFecha;      -- guarda: nunca pisar algo más nuevo
```

Si el terminal no existe → `HTTP 404`, que se trata como error de configuración
(el `ID_TERMINAL` está mal): se registra en `LogsSistema` local con nivel
`CRITICO` y **no** se cambia el nombre.

---

### 3.3 Actualizar la API — subir el nombre del NUC

```http
PUT https://bakeliteapi.sopytec.cl/api/terminal/1/nombre-terminal/desde-nuc
Content-Type: application/json
```

```json
{
  "nombre": "Torniquete Portería Norte",
  "nombreFecha": "2026-08-24T10:15:30-04:00",
  "nombrePor": "operador.nuc"
}
```

| Campo | Tipo | Obligatorio | Regla |
| --- | --- | --- | --- |
| `nombre` | string | Sí | 1 a 150 caracteres. |
| `nombreFecha` | ISO 8601 | Sí | Con zona. Es el momento real del cambio en el NUC, no el del envío. Máximo 5 minutos en el futuro. |
| `nombrePor` | string | No | Máximo 100 caracteres. Vacío se guarda como `NULL`. |

La API aplica el cambio **solo si `nombreFecha` es mayor que la suya**, y guarda
esa misma fecha (no `SYSDATETIMEOFFSET()`), con `NombreOrigen = 'LOCAL'`.

#### HTTP 200 — aplicado

```json
{
  "idTerminal": 1,
  "nombre": "Torniquete Portería Norte",
  "nombreFecha": "2026-08-24T10:15:30-04:00",
  "estado": "ACTUALIZADO"
}
```

#### HTTP 200 — la API ya tenía algo más nuevo

```json
{
  "idTerminal": 1,
  "nombre": "Torniquete Principal Norte",
  "nombreFecha": "2026-08-24T10:41:02-04:00",
  "estado": "RECHAZADO_POR_ANTIGUEDAD"
}
```

El cuerpo devuelve **el nombre y la fecha que quedaron vigentes en la API**. El
NUC adopta ese valor, exactamente igual que en el punto 3.2: su propio cambio
perdió la carrera y el conflicto queda cerrado en una sola llamada.

#### HTTP 200 — no había nada que cambiar

```json
{
  "idTerminal": 1,
  "nombre": "Torniquete Portería Norte",
  "nombreFecha": "2026-08-24T10:15:30-04:00",
  "estado": "SIN_CAMBIOS"
}
```

Se devuelve cuando el nombre enviado ya es idéntico al almacenado. Es la
respuesta esperada al reintentar un `PUT` que sí se aplicó pero cuya respuesta
se perdió: la operación es **idempotente**, reenviar el mismo par
`(nombre, nombreFecha)` nunca produce un cambio distinto.

| HTTP | Caso | Acción del NUC |
| --- | --- | --- |
| `200` con `ACTUALIZADO` o `SIN_CAMBIOS` | Subido. | `NombreSincronizado = 1`. |
| `200` con `RECHAZADO_POR_ANTIGUEDAD` | Perdió la carrera. | Adopta el nombre y la fecha del cuerpo. `NombreSincronizado = 1`. |
| `400` | Nombre vacío, muy largo, fecha sin zona o en el futuro. | Registra el error, deja el nombre local como está y **no reintenta** con el mismo cuerpo. |
| `404` | El `idTerminal` no existe. | Error de configuración, nivel `CRITICO`. No reintenta. |
| `429`, `5xx`, timeout o red | La API no está. | Deja `NombreSincronizado = 0` y reintenta más tarde con la **misma** `nombreFecha`. |

---

## 4. Cómo funciona desde cada lado

### 4.1 Cuando el operador cambia el nombre en el NUC

1. La aplicación valida el nombre y escribe en su tabla local:
   `Nombre`, `NombreFecha = SYSDATETIMEOFFSET()`, `NombreOrigen = 'LOCAL'`,
   `NombrePor = <usuario>`, `NombreSincronizado = 0`.
2. El cambio queda visible en pantalla **de inmediato**, haya red o no.
3. El sincronizador, en su ciclo normal, ve `NombreSincronizado = 0` y hace el
   `PUT` del punto 3.3.
4. Si la API no responde, la fila queda pendiente y se reintenta en el ciclo
   siguiente, siempre con la fecha original del cambio. Un corte de red no
   altera quién gana la comparación.

### 4.2 Cuando un administrador cambia el nombre en la web

1. BakeliteApi escribe `Nombre`, `NombreFecha = SYSDATETIMEOFFSET()` y
   `NombreOrigen = 'API'` en su propia tabla.
2. La API **no** empuja el cambio al NUC: el NUC no expone puertos entrantes.
   El cambio viaja en la siguiente comparación que haga el terminal.

### 4.3 Ciclo del NUC

- **Al iniciar la aplicación:** comparar (3.1) y actuar según el veredicto.
  Esto reemplaza la verificación de nombre que hoy solo dejaba una advertencia
  en el log.
- **Cada 5 minutos**, dentro del ciclo del sincronizador: comparar y actuar.
  Es una llamada liviana y sin escrituras; no necesita ser más frecuente porque
  el nombre no cambia seguido.
- **Inmediatamente después de un cambio local:** `PUT` directo, sin esperar el
  ciclo. Si falla, queda pendiente por `NombreSincronizado = 0`.
- **Sin conexión:** no se compara ni se sube nada. El nombre local sigue siendo
  el que ve el operador y el `PUT` pendiente espera. Al recuperar la conexión se
  resuelve todo en el primer ciclo.

### 4.4 Ejemplo completo

| Hora | Hecho | `Nombre` / `NombreFecha` en el NUC | En la API |
| --- | --- | --- | --- |
| 10:00 | Estado inicial | `Torniquete Principal` · 2026-08-20T09:02 | `Torniquete Principal` · 2026-08-20T09:02 |
| 10:15 | El operador renombra en el NUC (sin red) | `Portería Norte` · 10:15 · sync 0 | sin cambios |
| 10:20 | El administrador renombra en la web | igual | `Principal Norte` · 10:20 |
| 10:22 | Vuelve la red. El NUC compara: 10:20 > 10:15 | veredicto `ACTUALIZAR_LOCAL` | |
| 10:22 | El NUC hace `GET .../hacia-nuc` y adopta | `Principal Norte` · 10:20 · sync 1 | `Principal Norte` · 10:20 |

Ambos lados terminan con `Principal Norte` y con la **misma** `NombreFecha`
(10:20). El cambio de las 10:15 se pierde a propósito: es más antiguo. El
operador lo ve reflejado en pantalla en el mismo ciclo, así que sabe que su
edición fue reemplazada por una más reciente.

Si el NUC hubiera hecho el `PUT` de las 10:15 antes de que llegara el cambio de
las 10:20, la API lo habría aceptado y el cambio web posterior habría ganado
igual. El orden de llegada no cambia el resultado: solo cuenta la hora del
cambio.

---

## 5. Criterios de aceptación

Del lado de **BakeliteApi**:

1. `dbo.Terminales` tiene `NombreFecha` (`DATETIMEOFFSET`), `NombreOrigen` y
   `NombrePor`. La API de sincronización actualiza los tres campos de forma
   transaccional.
2. `POST .../comparar` no modifica ninguna fila y devuelve los tres
   veredictos según las reglas del punto 3.1.
3. `PUT .../desde-nuc` aplica el cambio solo si la fecha recibida es mayor, guarda
   esa fecha tal cual, y devuelve `RECHAZADO_POR_ANTIGUEDAD` con el valor
   vigente cuando no lo es.
4. Reenviar el mismo `PUT` devuelve `SIN_CAMBIOS` y no crea otra modificación.
5. Un `idTerminal` inexistente devuelve `404` en los tres endpoints.
6. Una fecha sin zona horaria, o más de 5 minutos en el futuro, devuelve `400`.

Del lado del **NUC**:

7. Renombrar sin conexión funciona, se ve en pantalla y queda pendiente.
8. Al recuperar la conexión, el cambio pendiente se sube con su fecha original.
9. Un nombre más nuevo en la API se adopta al iniciar y en el ciclo periódico,
   conservando la `nombreFecha` remota.
10. El nombre local nunca se sobrescribe con uno de fecha anterior.
11. Un `404` no cambia el nombre y queda registrado como error de configuración
    de nivel `CRITICO`.

---

Este contrato no reemplaza el envío de marcas, el health check ni el registro de
incidentes. Se aplica junto con:

- `CONTRATO_INTEGRACION_TORNIQUETE.md`;
- `CONTRATO_ENDPOINTS_PENDIENTES.md` (cuyo punto 6 y criterio 7 quedan anulados
  por este documento);
- `CONTRATO_DISPONIBILIDAD_SOFTWARE_ESCRITORIO.md`.
