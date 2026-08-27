# Contrato de lectoras y relés del terminal

**Versión:** 1.0
**Fecha:** 2026-08-26
**Componentes:** app Python del NUC, Bakelite API, Bakelite Web
**Base URL de producción:** `https://bakeliteapi.sopytec.cl`

**Estado:** implementación incorporada en Bakelite API y Bakelite Web. Requiere
aplicar la migración `013_TerminalDevices.sql` y publicar ambos proyectos.

## Cómo leer este documento

Son dos partes en un solo archivo, a propósito: no hace falta abrir ningún otro.

- **§1 a §8 — la especificación** de lectoras y relés: tablas, endpoints,
  reglas y criterios de aceptación. Es lo nuevo que hay que construir.
- **§9 — el resumen de pendientes** de *toda* la integración, no solo de este
  contrato: qué falta en la API, qué falta en la web y qué falta en el NUC.

**Si solo quiere saber qué le toca a su equipo, vaya directo a §9.**

---

## 1. Qué resuelve

Hoy la configuración de las lectoras y los relés vive **solo en el NUC**: qué
lectora es ENTRADA, cuál es SALIDA, y lo mismo para los relés que abren cada
torniquete. Desde la web no se ve ni se puede corregir, y si el equipo se
reinstala esa configuración se pierde.

Este contrato cubre dos cosas que conviene no confundir:

| | Quién lo origina | Quién lo guarda | Regla |
| --- | --- | --- | --- |
| **Configuración** — qué dispositivo es ENTRADA o SALIDA, su nombre, si está de alta | Los dos lados | Los dos | Gana el último cambio |
| **Telemetría física** — si está conectada, en qué puerto, cuándo leyó por última vez | Solo el NUC | NUC de forma persistente; API solo en memoria | Lo informa el NUC |

La **configuración se guarda en las dos bases de datos**. La telemetría física
se guarda de forma permanente solamente en la base local del NUC. El NUC envía
una fotografía cada 10 segundos y la API la conserva temporalmente en memoria,
separada por `IdTerminal`, para que la web pueda verla sin conectarse al equipo.
Si la API se reinicia, esa fotografía se pierde y vuelve a estar disponible en
el siguiente envío del NUC.

La diferencia entre las dos filas es **quién origina el dato**, no quién lo
almacena. La configuración es una decisión humana y se puede tomar desde
cualquiera de los dos lados. El estado es un hecho físico que solo puede
observar el equipo que tiene el cable enchufado: la API lo conserva
transitoriamente en memoria y lo publica, pero jamás lo inventa ni lo deduce.

---

## 2. Regla de precedencia

**Gana el cambio más reciente.** Cada lado sella la hora exacta en que cambió la
configuración y esa hora es el criterio.

**En caso de empate exacto al segundo, gana el NUC.**

> ⚠️ **Ojo: esto es al revés que en `CONTRATO_SINCRONIZACION_NOMBRE_TERMINAL.md`**,
> donde un empate lo gana la API. No es una inconsistencia, es a propósito: el
> nombre del terminal es un dato administrativo y la web es su lugar natural,
> pero cuál lectora es la de entrada es un hecho del mundo físico. Quien está
> parado frente al torniquete con el cable en la mano tiene mejor información
> que quien mira una pantalla a distancia. Ante la duda, manda el torniquete.

Ambos lados deben implementar el desempate igual, o cada uno llegaría a una
conclusión distinta sobre el mismo par de fechas.

---

## 3. Estructura de las tablas

Las dos tablas existen en ambos lados con los mismos campos de configuración.
En el NUC incluyen además sus campos locales de sincronización y telemetría;
esos campos no se replican en SQL Server.

### 3.1 `dbo.Lectoras`

```sql
CREATE TABLE dbo.Lectoras (
    IdTerminal        INT               NOT NULL,
    Numero            INT               NOT NULL,   -- 1, 2, … físico del equipo

    /* --- Configuración: se sincroniza en ambos sentidos --- */
    Sentido           CHAR(1)           NOT NULL,   -- E = ENTRADA, S = SALIDA
    Descripcion       NVARCHAR(150)     NULL,
    Activa            BIT               NOT NULL
        CONSTRAINT DF_Lectoras_Activa DEFAULT (1),
    ConfigFecha       DATETIMEOFFSET(0) NOT NULL
        CONSTRAINT DF_Lectoras_ConfigFecha DEFAULT (SYSDATETIMEOFFSET()),
    ConfigOrigen      VARCHAR(10)       NOT NULL
        CONSTRAINT DF_Lectoras_ConfigOrigen DEFAULT ('LOCAL'),
    ConfigPor         NVARCHAR(100)     NULL,
    IdCambio          CHAR(32)          NOT NULL,

    CONSTRAINT PK_Lectoras PRIMARY KEY (IdTerminal, Numero),
    CONSTRAINT FK_Lectoras_Terminal FOREIGN KEY (IdTerminal)
        REFERENCES dbo.Terminales (IdTerminal),
    CONSTRAINT CK_Lectoras_Sentido CHECK (Sentido IN ('E','S')),
    CONSTRAINT CK_Lectoras_Origen  CHECK (ConfigOrigen IN ('LOCAL','API'))
);
GO

/* Un terminal tiene una ENTRADA y una SALIDA, no dos iguales. El filtro deja
   fuera las dadas de baja, que sí pueden repetir sentido con las activas. */
CREATE UNIQUE INDEX UX_Lectoras_Sentido
    ON dbo.Lectoras (IdTerminal, Sentido) WHERE Activa = 1;
GO
```

### 3.2 `dbo.Reles`

Idéntica, salvo dos columnas propias:

```sql
CREATE TABLE dbo.Reles (
    IdTerminal        INT               NOT NULL,
    Numero            INT               NOT NULL,

    Sentido           CHAR(1)           NOT NULL,
    Comando           VARCHAR(10)       NOT NULL,   -- ASCII del Arduino: R1*, R2*
    Descripcion       NVARCHAR(150)     NULL,
    Activo            BIT               NOT NULL
        CONSTRAINT DF_Reles_Activo DEFAULT (1),
    ConfigFecha       DATETIMEOFFSET(0) NOT NULL
        CONSTRAINT DF_Reles_ConfigFecha DEFAULT (SYSDATETIMEOFFSET()),
    ConfigOrigen      VARCHAR(10)       NOT NULL
        CONSTRAINT DF_Reles_ConfigOrigen DEFAULT ('LOCAL'),
    ConfigPor         NVARCHAR(100)     NULL,
    IdCambio          CHAR(32)          NOT NULL,

    CONSTRAINT PK_Reles PRIMARY KEY (IdTerminal, Numero),
    CONSTRAINT FK_Reles_Terminal FOREIGN KEY (IdTerminal)
        REFERENCES dbo.Terminales (IdTerminal),
    CONSTRAINT CK_Reles_Sentido CHECK (Sentido IN ('E','S')),
    CONSTRAINT CK_Reles_Origen  CHECK (ConfigOrigen IN ('LOCAL','API'))
);
GO

CREATE UNIQUE INDEX UX_Reles_Sentido
    ON dbo.Reles (IdTerminal, Sentido) WHERE Activo = 1;
GO
```

### 3.3 Notas de diseño

En la API existen además `dbo.TerminalConfiguracionDispositivos`, que conserva
`ConfigVersion` por terminal, y `dbo.CambiosConfiguracionDispositivos`, que
garantiza la idempotencia de `idCambio`. La estructura ejecutable y sus
restricciones están en `013_TerminalDevices.sql`.

Los campos `Sincronizado`, `Conectada`, `Puerto`, `UltimaLectura`,
`UltimoDisparo`, `UltimoError` y el estado del Arduino pertenecen a la base
local del NUC. La API no los persiste: mantiene solamente la última fotografía
en una caché volátil en memoria.

**`IdTerminal` va en la clave primaria y en todos los payloads.** Un mismo
número de lectora existe en cada terminal, así que `Numero` por sí solo no
identifica nada. Sin el `IdTerminal`, un mensaje mal enrutado reconfiguraría el
equipo equivocado sin que nada lo delate.

**Las bajas son lógicas (`Activa`/`Activo` = 0), nunca `DELETE`.** Las marcas y
los errores ya registrados apuntan a esos dispositivos; borrarlos rompería el
historial. Además, un `DELETE` propagado por error es irreversible, mientras que
una baja se revierte con un `UPDATE`.

**`ConfigFecha` es aparte de cualquier `FechaModificacion` general.** Corregir
una descripción no debe hacer que un sentido viejo gane la comparación.

**Sincronía de reloj:** ambos servidores con NTP. Todas las fechas viajan en
**ISO 8601 con offset explícito** (`2026-08-26T10:15:30-04:00`), nunca sin zona.
La comparación se hace en UTC.

### 3.4 Migración del NUC

El NUC ya tiene ambas tablas con `Numero` como clave y sin `IdTerminal`. La
migración debe agregar `IdTerminal` con el valor de `config.ID_TERMINAL`,
rehacer la clave primaria y agregar las columnas de sincronización y estado. Las
filas existentes toman `ConfigOrigen = 'LOCAL'` y `ConfigFecha` = su
`FechaModificacion` o, si es nula, la de creación del terminal.

---

## 4. Endpoints

Anónimos, con el limitador de solicitudes del terminal y `Cache-Control:
no-store`, igual que el resto de endpoints del terminal.

| Método y ruta | Uso |
| --- | --- |
| `POST /api/terminal/{idTerminal}/dispositivos/sincronizar` | El NUC manda todo y recibe lo que allá sea más nuevo. |
| `GET /api/terminal/{idTerminal}/dispositivos` | Lectura para la web y para diagnóstico. |

Estos son los dos endpoints del NUC. Como el NUC envía la foto completa —configuración y estado— cada
pocos segundos, no hacen falta endpoints separados para comparar, subir y bajar:
ese mismo envío es la comparación, la subida y la bajada, en un viaje.

Ambos responden `404` si el `idTerminal` no existe, y `409` si el terminal está
inactivo.

---

### 4.1 Sincronizar: el envío periódico

Es el corazón del contrato. El NUC manda **todo lo que sabe** de sus
dispositivos: cómo están configurados, con la fecha de cada configuración, y en
qué estado están ahora mismo.

```http
POST /api/terminal/1/dispositivos/sincronizar
Content-Type: application/json
```

```json
{
  "idTerminal": 1,
  "idCambio": "9f1c8a7e5b2d4f0a9c3e6b1d8f4a2c70",
  "lectoras": [
    {
      "numero": 1,
      "sentido": "E",
      "descripcion": "Lectora 1",
      "activa": true,
      "configFecha": "2026-08-26T10:15:30-04:00",
      "configPor": "operador",
      "conectada": true,
      "puerto": "/dev/ttyUSB0",
      "ultimaLectura": "2026-08-26T11:39:58-04:00",
      "ultimoError": null
    },
    {
      "numero": 2,
      "sentido": "S",
      "descripcion": "Lectora 2",
      "activa": true,
      "configFecha": "2026-08-26T10:15:30-04:00",
      "configPor": "operador",
      "conectada": false,
      "puerto": null,
      "ultimaLectura": "2026-08-26T09:12:44-04:00",
      "ultimoError": "Puerto perdido"
    }
  ],
  "reles": [
    {
      "numero": 1,
      "sentido": "E",
      "comando": "R2*",
      "activo": true,
      "configFecha": "2026-08-20T09:00:00-04:00",
      "configPor": "instalacion",
      "ultimoDisparo": "2026-08-26T11:39:59-04:00",
      "ultimoError": null
    }
  ],
  "arduino": { "conectado": true, "puerto": "/dev/ttyACM0" }
}
```

| Campo | Tipo | Obligatorio | Regla |
| --- | --- | --- | --- |
| `idTerminal` | integer | Sí | Debe coincidir con el de la ruta; si no, `400`. |
| `idCambio` | UUID string | **Sí, siempre** | Lo crea el NUC y lo reutiliza en cada reintento mientras el cambio siga pendiente. Va aunque el NUC no tenga nada pendiente: si el dispositivo no existe todavía en Bakelite, el envío lo da de alta y eso también es un cambio. |
| `numero` | integer | Sí | ≥ 1. Junto con `idTerminal` identifica al dispositivo. |
| `sentido` | string | Sí | `E` o `S`. |
| `comando` | string | Solo relés | 1 a 10 caracteres. |
| `configFecha` | ISO 8601 | Sí | Con zona. Máximo 5 minutos en el futuro. |
| `activa` / `activo` | boolean | No | Por defecto `true`. Baja lógica. |
| `conectada`, `puerto`, `ultimaLectura`, `ultimoDisparo`, `ultimoError` | — | No | Estado observado. El NUC lo persiste; la API solo conserva la última fotografía en memoria. |
| `conectado` (solo relés) | boolean | No | Un relé no tiene puerto propio: lo acciona el Arduino, así que **su conexión es la del Arduino**. El NUC lo deriva de ahí en cada envío en vez de guardarlo aparte, para que las dos copias no se desincronicen. Sin este campo la web muestra el relé como «Sin estado» indefinidamente. |

**Qué hace la API al recibirlo,** por dispositivo:

1. **Actualiza la telemetría en memoria, siempre.** No la escribe en SQL
   Server. Sella la fotografía con la hora de recepción de la API.
2. **Compara la configuración.** Si la `configFecha` del NUC es mayor que la
   suya, la aplica con `ConfigOrigen = 'LOCAL'` y **guarda esa misma fecha**, no
   la de recepción.
3. **Si la suya es más reciente, no la toca** y la devuelve en la respuesta para
   que el NUC la adopte.
4. **Si el dispositivo no existe en la API, lo crea.** Si existe allá y no vino
   en el envío, lo devuelve para que el NUC lo cree.

#### Respuesta `200 OK`

```json
{
  "idTerminal": 1,
  "configVersion": 48,
  "fechaHoraServidor": "2026-08-26T11:40:12-04:00",
  "sincronizarCadaSegundos": 10,
  "aplicar": {
    "lectoras": [
      { "numero": 2, "sentido": "E", "descripcion": "Puerta lateral",
        "activa": true, "configFecha": "2026-08-26T11:41:00-04:00",
        "configOrigen": "API", "configPor": "admin.bakelite",
        "idCambio": "3a7d0e1b9c5f4826b0d3a9e7c1f60482" }
    ],
    "reles": []
  },
  "resultados": [
    { "tipo": "lectora", "numero": 1, "estado": "SIN_CAMBIOS" },
    { "tipo": "lectora", "numero": 2, "estado": "RECHAZADO_POR_ANTIGUEDAD" }
  ]
}
```

**`aplicar` es la lista de lo que el NUC debe adoptar**, ya resuelto por la API:
solo los dispositivos donde Bakelite tiene algo más reciente. Si va vacío, el
NUC no hace nada. Ese es el mecanismo por el que un cambio hecho en la web llega
al equipo.

El NUC guarda **la `configFecha` recibida, no la hora en que la aplicó**. Si
guardara la hora de aplicación, su copia parecería siempre más nueva y el cambio
rebotaría de vuelta en el envío siguiente. Al escribir usa la guarda
`AND ConfigFecha < @configFecha`, para no pisar un cambio local más reciente si
los mensajes llegan desordenados.

| `estado` en `resultados` | Significado | Qué hace el NUC |
| --- | --- | --- |
| `ACTUALIZADO` | La API adoptó lo del NUC. | `Sincronizado = 1`. |
| `SIN_CAMBIOS` | Ya estaban iguales. | `Sincronizado = 1`. |
| `RECHAZADO_POR_ANTIGUEDAD` | La API tenía algo más nuevo. | Adopta lo que viene en `aplicar`. |
| `CREADO` | La API no lo tenía y lo dio de alta. | `Sincronizado = 1`. |

La operación es **idempotente**: reenviar el mismo `idCambio` con el mismo
contenido nunca produce un resultado distinto. Un envío sin cambios de
configuración es simplemente un refresco de estado.

#### Cuándo se envía

- Al **iniciar** la aplicación.
- **Cada vez que algo cambia**: se cambia un sentido desde Ajustes, una lectora
  se enchufa o se desconecta, un relé falla.
- Y como **refresco cada 10 segundos** aunque nada haya cambiado, para que la web
  pueda distinguir "todo sigue igual" de "el NUC dejó de informar".

Es el mismo ritmo que el heartbeat y que el sondeo de salud: todo lo
periódico del terminal late cada 10 segundos.

El intervalo lo manda la API en `sincronizarCadaSegundos`: los clientes no lo
fijan por su cuenta.

Un cambio hecho **en el NUC** sube al instante, porque dispara un envío. Un
cambio hecho **en la web** baja en el envío siguiente: como máximo 10 segundos,
y de inmediato si el equipo estaba informando cualquier otra cosa.

`configVersion` es un entero que la API incrementa con **cualquier** cambio de
configuración de ese terminal, venga de donde venga. Sirve para que la web sepa
que algo cambió sin comparar campo por campo, y para diagnóstico.

---

### 4.2 Lectura para la web

```http
GET /api/terminal/1/dispositivos
```

Devuelve la configuración persistida en SQL Server combinada con la última
telemetría disponible en memoria, más la antigüedad de esa fotografía:

```json
{
  "idTerminal": 1,
  "configVersion": 48,
  "fechaHoraServidor": "2026-08-26T11:40:20-04:00",
  "estadoFecha": "2026-08-26T11:40:12-04:00",
  "estadoAntiguoSegundos": 8,
  "lectoras": [
    { "numero": 1, "sentido": "E", "descripcion": "Lectora 1", "activa": true,
      "configFecha": "2026-08-26T10:15:30-04:00", "configOrigen": "LOCAL",
      "conectada": true, "puerto": "/dev/ttyUSB0",
      "ultimaLectura": "2026-08-26T11:39:58-04:00", "ultimoError": null }
  ],
  "reles": [ /* misma forma, con "comando", "activo" y "ultimoDisparo" */ ],
  "arduino": { "conectado": true, "puerto": "/dev/ttyACM0" }
}
```

**Presentación obligatoria en la web.** El estado tiene fecha, y un dato viejo no
es un dato: si `estadoAntiguoSegundos` supera **30 segundos** (tres veces el
refresco), la web debe mostrarlo como **no confirmado**, en gris, y nunca como
"conectada". Un indicador verde sostenido por una lectura de hace una hora es
peor que no mostrar nada.

### 4.3 Cambios hechos desde la web

La web modifica la configuración mediante endpoints autenticados:

- `PUT /api/terminals/{id}/devices/readers/{numero}`;
- `PUT /api/terminals/{id}/devices/relays/{numero}`.

Al hacerlo la API debe:

1. sellar `ConfigFecha` con la hora del servidor;
2. poner `ConfigOrigen = 'API'` y `ConfigPor` con el usuario;
3. generar un `idCambio` y registrarlo (§5);
4. incrementar `configVersion`.

El cambio queda esperando: viaja al NUC en el siguiente envío de sincronización,
dentro de la respuesta `aplicar`. La API **no** necesita conectarse al equipo, y
de hecho no puede: el NUC no acepta conexiones entrantes.

---

## 5. Registro obligatorio en ambos lados

**Todo cambio de configuración, alta, baja, conflicto resuelto y error de
sincronización debe quedar registrado en los dos sistemas.** No es opcional:
cuando algo salga mal en terreno, la única forma de reconstruir qué pasó es
tener las dos versiones de la historia y poder cruzarlas.

| Lado | Dónde |
| --- | --- |
| NUC | `dbo.Errores` (`Origen`, `Nivel`, `Mensaje`, `Detalle`, `IdTerminal`) |
| API | `LogsSistema`, con el mismo criterio que el resto de operaciones del terminal |

### 5.1 El `idCambio` es lo que permite cruzarlos

Cada operación que modifica configuración lleva un `idCambio` (UUID de 32
caracteres sin guiones). **Ambos lados deben escribirlo en el texto del
registro.** Sin él, dos entradas que hablan del mismo hecho quedan como sucesos
inconexos en dos bases distintas.

- Si el cambio nace en el NUC, el `idCambio` lo genera el NUC y viaja en el `POST` de sincronización.
- Si nace en la web, lo genera la API y viaja al NUC dentro de `aplicar`, en
  la respuesta de la sincronización.

### 5.2 Qué se registra

| Hecho | Nivel | Ejemplo de mensaje |
| --- | --- | --- |
| Cambio de configuración aplicado | `INFO` | `Lectora 2 pasa a SALIDA (idCambio 9f1c…, origen API, por admin.bakelite)` |
| Alta de dispositivo | `INFO` | `Alta de relé 3 (idCambio …)` |
| Baja de dispositivo | `WARN` | `Baja del relé 2 (idCambio …)` |
| Conflicto resuelto | `WARN` | `Conflicto en lectora 1: gana LOCAL 11:40:05 sobre API 11:39:50 (idCambio …)` |
| Cambio rechazado por antigüedad | `WARN` | `Rechazado el cambio … por ser anterior al vigente` |
| Cambio rechazado por validación | `ERROR` | `HTTP 400: sentido inválido …` |
| Terminal inexistente o inactivo | `CRITICO` | `El idTerminal 1 no existe / está inactivo` |
| Dispositivo desconectado / reconectado | `WARN` / `INFO` | `Lectora 2 desconectada (puerto perdido)` |

El registro de un conflicto debe incluir **las dos fechas y el ganador**. Decir
solo "se actualizó" no permite después saber si el desempate se aplicó bien.

### 5.3 Lo que NO se registra

El refresco periódico de estado cuando nada cambió. Son 2.880 mensajes por día y
por terminal que taparían todo lo demás. Solo se registran los **cambios** de
estado.

---

## 6. Conducta ante errores

| HTTP | Significado | Conducta del NUC |
| --- | --- | --- |
| `200` | Aceptado. | Continuar. |
| `400` | Datos inválidos. | `ERROR` en `dbo.Errores`, **no reintentar el mismo cuerpo**, dejar el cambio marcado como fallido para revisión. |
| `404` | El terminal no existe. | `CRITICO`, error de configuración, no reintentar al mismo ritmo. |
| `409` | El terminal está inactivo. | `CRITICO`, avisar al operador y detener la sincronización de configuración. |
| `429` | Límite de solicitudes. | Respetar `Retry-After`. |
| `5xx`, timeout, red | Fallo temporal. | Mantener pendiente y reintentar en el ciclo siguiente, **con la misma `configFecha` y el mismo `idCambio`**. |

Sin conexión, **el NUC sigue operando con su configuración local**: el control
de acceso no puede depender de que Bakelite esté disponible. Los cambios quedan
pendientes (`Sincronizado = 0`) y suben al reconectar.

---

## 7. Criterios de aceptación

### Bakelite API

1. Existen `dbo.Lectoras` y `dbo.Reles` con `IdTerminal` en la clave primaria y
   FK a `dbo.Terminales`.
2. El índice único impide dos dispositivos activos con el mismo sentido en un
   terminal.
3. `POST .../sincronizar` **siempre** actualiza la fotografía de telemetría en
   memoria, aunque la configuración no cambie.
4. Aplica la configuración **dispositivo por dispositivo**, solo donde la fecha
   del NUC sea mayor, y guarda esa misma fecha —no la de recepción.
5. Devuelve en `aplicar` los dispositivos donde la API tiene algo más reciente,
   y solo esos.
6. Da de alta los dispositivos que llegan y no existían; devuelve los que existen
   en la API y no vinieron.
7. Reenviar el mismo `idCambio` devuelve `SIN_CAMBIOS` y no duplica nada.
8. `configVersion` aumenta con **cualquier** cambio de configuración, venga de la
   web o del NUC.
9. Un empate exacto de fechas se resuelve a favor del NUC.
10. Todo cambio, conflicto y error queda en `LogsSistema` **con su `idCambio`**.
11. Un `idTerminal` inexistente devuelve `404`; uno inactivo, `409`.
12. Un cambio hecho desde la web sella hora del servidor, `ConfigOrigen = 'API'`,
    genera `idCambio` e incrementa `configVersion`.

### App Python del NUC

13. Migra sus tablas a la estructura de §3 sin perder la configuración vigente.
14. Guarda en su base local tanto la configuración como el estado observado.
15. Sincroniza al iniciar, ante cada cambio y cada 10 segundos.
16. Aplica lo que venga en `aplicar` conservando la `configFecha` recibida.
17. Un cambio local sube al instante y, sin conexión, queda pendiente con su
    fecha original y el mismo `idCambio`.
18. Nunca sobrescribe una configuración local con una de fecha anterior.
19. Sigue operando con su configuración local aunque la API no responda.
20. Todo cambio, conflicto y error queda en `dbo.Errores` **con su `idCambio`**.

### Bakelite Web

21. Muestra estado y configuración de cada dispositivo, con su `IdTerminal`.
22. Permite cambiar sentido, descripción y alta/baja, generando un `idCambio`.
23. Marca el estado como **no confirmado** si supera los 30 segundos de
    antigüedad, y nunca lo muestra como conectado.

---

## 8. Relación con los otros contratos

Este contrato **no reemplaza** ninguno de los existentes y se aplica junto con:

- `CONTRATO_INTEGRACION_TORNIQUETE.md` — envío de marcas;
- `CONTRATO_ENDPOINTS_PENDIENTES.md` — health e incidentes vistos por el NUC;
- `CONTRATO_SINCRONIZACION_NOMBRE_TERMINAL.md` — nombre del terminal (misma
  mecánica de last-write-wins, **pero con el desempate al revés**, ver §2);
- `CONTRATO_DISPONIBILIDAD_SOFTWARE_ESCRITORIO.md` — presencia del proceso
  Python. Ese contrato aclara que `EN_LINEA` no dice nada del hardware: **este
  documento es el que cubre esa parte**.

---

## 9. Pendientes de integración

Esta sección resume **toda** la integración entre el NUC y Bakelite, no solo
este contrato. Sirve para saber qué está esperando a quién sin leer los cinco
documentos completos.

### 9.1 Estado general

| Contrato | API | Web | NUC (Python) |
| --- | :---: | :---: | :---: |
| `CONTRATO_INTEGRACION_TORNIQUETE.md` — envío de marcas | ✅ | — | ✅ |
| `CONTRATO_ENDPOINTS_PENDIENTES.md` — health, incidentes, datos del terminal | ✅ | — | ✅ |
| `CONTRATO_SINCRONIZACION_NOMBRE_TERMINAL.md` — nombre | ✅ | ⚠️ | ✅ |
| `CONTRATO_DISPONIBILIDAD_SOFTWARE_ESCRITORIO.md` — presencia | ✅ | ⚠️ | ✅ |
| **Este documento** — lectoras y relés | ✅* | ✅* | ✅ |

✅ hecho · ✅* implementado, pendiente de migración/publicación · ⚠️ falta un detalle · ❌ sin empezar · 🟡 parcial

---

### 9.2 Lo que falta en Bakelite API

#### Lectoras y relés — implementado, pendiente de despliegue

Especificación completa: **§1 a §8 de este mismo documento**.

Se incorporaron:

- las tablas de configuración `dbo.Lectoras` y `dbo.Reles`, con `IdTerminal` en
  la clave primaria y FK a `dbo.Terminales` (§3);
- `dbo.TerminalConfiguracionDispositivos` y
  `dbo.CambiosConfiguracionDispositivos`;
- `POST /api/terminal/{idTerminal}/dispositivos/sincronizar`;
- `GET /api/terminal/{idTerminal}/dispositivos`;
- endpoints autenticados de administración para la web;
- caché volátil de telemetría por terminal;
- registro en `LogsSistema` con `idCambio`.

Antes de usarlo en producción debe ejecutarse `013_TerminalDevices.sql`,
publicarse la API y luego publicarse la compilación actual de Bakelite Web.

**Dos puntos que conviene no pasar por alto:**

1. **El desempate va a favor del NUC**, al revés que en el contrato del nombre.
   Está explicado en §2: la configuración de un dispositivo es
   un hecho físico, y quien tiene el cable enchufado tiene mejor información.

2. **Un cambio de sentido mueve siempre a los dos dispositivos.** Si se marca la
   lectora 1 como ENTRADA, la 2 pasa a SALIDA por definición. Aplicarlo de a uno
   viola el índice único a mitad de camino: tiene que ser una sola sentencia. Lo
   descubrimos implementándolo de este lado, y les ahorramos el hallazgo.

#### Sincronización de incidentes de ausencia — opcional

`CONTRATO_DISPONIBILIDAD_SOFTWARE_ESCRITORIO.md` §8:
`GET /api/terminal/{id}/availability/incidents`.

Está implementado del lado de la API y **el NUC todavía no lo consume**. El
propio contrato lo marca como opcional, así que no bloquea nada.

> Cuando lo hagamos, esos incidentes irán a una **tabla aparte** en el NUC. No
> son los mismos que ya subimos por `POST /api/terminal/incidents`: los nuestros
> son cortes que vio el terminal ("no pude alcanzar la API") y los de ustedes
> son ausencias que vio la API ("el terminal dejó de latir"). Ante un corte de
> red, ambos lados abren su propio incidente del mismo hecho. Mezclarlos haría
> imposible saber quién observó qué.

---

### 9.3 Lo que falta en Bakelite Web

#### Estado `DESCONOCIDO_API` tras 6 segundos

`CONTRATO_DISPONIBILIDAD_SOFTWARE_ESCRITORIO.md` §6. Ya está señalado como
pendiente en la tabla §10 de ese contrato.

Sin eso, si la propia API deja de responder, la web puede quedar mostrando
indefinidamente un "En línea" viejo, que es peor que no mostrar nada.

#### Aviso del retardo al renombrar el terminal

`CONTRATO_SINCRONIZACION_NOMBRE_TERMINAL.md` §4.2.

Un cambio de nombre hecho desde la web tarda **hasta 60 segundos** en verse en
la pantalla del terminal, porque es el terminal quien pregunta. La web debe
avisarlo al guardar:

> *"Nombre actualizado. El terminal lo mostrará dentro de 1 minuto."*

Sin ese aviso, quien renombra ve la web actualizada, mira el torniquete, lo ve
con el nombre viejo y concluye que algo falló.

El sentido contrario no necesita aviso: un cambio hecho en el terminal sube al
instante.

#### Pantalla de lectoras y relés

La sección **Dispositivos** ya permite seleccionar un terminal, cambiar sentido,
descripción y alta/baja, y visualizar lectoras, relés y Arduino. Está preparada
para varios terminales aunque inicialmente exista solo uno.

**El estado tiene fecha y hay que respetarla:** si `estadoAntiguoSegundos` supera
**30 segundos**, se muestra en gris como *no confirmado*, nunca como "conectada".
Un indicador verde sostenido por una lectura de hace una hora engaña.

---

### 9.4 Lo que falta en el NUC

| Pendiente | Bloqueado por |
| --- | --- |
| Envío `POST .../dispositivos/sincronizar` cada 10 s | Pendiente de desplegar la API actualizada |
| Aplicar los cambios que lleguen en `aplicar` | Pendiente de prueba integrada tras el despliegue |
| Consumir `GET .../availability/incidents` | Nada: es opcional y está postergado |

Todo lo demás está hecho y probado contra la API de producción.

**Ya listo de este lado**, aunque el endpoint no exista: la base de datos local
guarda la configuración de cada dispositivo con su `ConfigFecha`, `ConfigOrigen`,
`ConfigPor` y su marca de pendiente, y también el estado observado —si está
conectada, en qué puerto, cuándo leyó, cuándo disparó cada relé y el estado del
Arduino—. Cuando el endpoint exista, solo hay que enchufar el envío.

---

### 9.5 Ritmos acordados

Todo lo periódico del terminal contra Bakelite late cada **10 segundos**, con una
excepción deliberada:

| Ciclo | Intervalo |
| --- | ---: |
| Envío de marcas pendientes | 10 s |
| Sondeo de salud (`GET /api/terminal/health`) | 10 s |
| Heartbeat de presencia | 10 s |
| Sincronización de dispositivos (cuando exista) | 10 s |
| **Comparación del nombre del terminal** | **60 s** |

El nombre va más espaciado porque la comparación **no escribe nada** y el nombre
cambia cada varios meses: a 10 s serían 8.640 llamadas diarias por terminal para
no encontrar nada. De ahí el aviso de §3.2.

---

### 9.6 Una petición sobre los registros

En todos los contratos pedimos que **cada cambio, conflicto y error quede
registrado en los dos lados** — en `LogsSistema` allá y en `dbo.Errores` acá.

Lo que hace que eso sirva de algo es el **`idCambio`**: un UUID que viaja en la
operación y que **ambos lados deben escribir en el texto del registro**. Sin él
quedan dos entradas que hablan del mismo hecho en dos bases distintas, sin forma
de cruzarlas.

Cuando algo falle en terreno, esa correlación es la diferencia entre reconstruir
qué pasó en diez minutos o no poder hacerlo.

Y al registrar un conflicto, incluyan **las dos fechas y el ganador**. Decir solo
"se actualizó" no permite después verificar si el desempate se aplicó bien.
