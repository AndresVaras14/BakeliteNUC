# Contrato de endpoints pendientes para el NUC (Python)

**Destinatario:** equipo responsable del software Python instalado en el NUC.  
**Vigencia:** 2026-08-24.  
**Estado:** endpoints implementados en BakeliteApi; integración pendiente o por
verificar en el software Python.

Este es el contrato que debe enviarse al equipo del NUC para implementar los
tres endpoints adicionales al envío de marcas definido en
`CONTRATO_INTEGRACION_TORNIQUETE.md`.

La disponibilidad del propio software Python mediante heartbeat es otro flujo
y se mantiene separada en `CONTRATO_DISPONIBILIDAD_SOFTWARE_ESCRITORIO.md`.

Base URL de producción:

```text
https://bakeliteapi.sopytec.cl
```

Los endpoints son anónimos, usan el limitador de solicitudes del terminal y
responden con `Cache-Control: no-store`.

| Método y ruta | Uso |
| --- | --- |
| `GET /api/terminal/health` | Comprobar API y base de datos en tiempo real. |
| `POST /api/terminal/incidents` | Registrar un corte recuperado, sin duplicarlo al reintentar. |
| `GET /api/terminal/{idTerminal}` | Obtener el nombre y estado autoritativos del terminal. |

---

## 1. Estado de la API

```http
GET https://bakeliteapi.sopytec.cl/api/terminal/health
```

No lleva parámetros, cuerpo ni autenticación. Hace una comprobación real del
esquema en SQL Server con timeout corto. Este sondeo no se guarda en
`LogsSistema`, porque el terminal lo ejecuta cada 20 segundos.

### HTTP 200

```json
{
  "estado": "OK",
  "baseDatos": "OK",
  "fechaHora": "2026-08-20T15:47:12-04:00",
  "version": "1.0.0"
}
```

`version` es la última versión registrada en `dbo.SistemaVersiones`, usando el
mismo criterio que el login y sin eliminar el historial.

### HTTP 503

```json
{
  "estado": "DEGRADADO",
  "baseDatos": "ERROR",
  "fechaHora": "2026-08-20T15:47:12-04:00",
  "detalle": "No fue posible verificar la base de datos dentro del tiempo esperado."
}
```

El detalle es deliberadamente genérico: no expone servidor, base, usuario ni
excepciones internas.

El terminal considera que hay conexión solamente ante `HTTP 200` y
`baseDatos = "OK"`. Ante `503`, otro `5xx`, `408`, `429`, `404`, timeout o error
de red conserva su cola local y vuelve a intentar después.

---

## 2. Registro de incidentes de conexión

El terminal envía el incidente cuando el servicio ya se recuperó. Durante la
caída lo conserva en su propia base de datos.

```http
POST https://bakeliteapi.sopytec.cl/api/terminal/incidents
Content-Type: application/json
```

```json
{
  "idIncidente": "77bb00d6-222d-4610-8707-d352a9744128",
  "idTerminal": 1,
  "servicio": "BAKELITE",
  "fechaDeteccion": "2026-08-20T12:30:51-04:00",
  "fechaRecuperacion": "2026-08-20T12:34:07-04:00",
  "duracionSegundos": 196,
  "intentosFallidos": 18,
  "detalle": "Timeout consultando la API"
}
```

| Campo | Tipo | Obligatorio | Regla |
| --- | --- | --- | --- |
| `idIncidente` | UUID string | Sí | Lo crea el terminal una sola vez y reutiliza exactamente el mismo valor en todos los reintentos. Acepta UUID con o sin guiones. |
| `idTerminal` | integer | Sí | Debe existir en `dbo.Terminales`. |
| `servicio` | string | Sí | `BAKELITE` o `EXTERNA`. |
| `fechaDeteccion` | ISO 8601 | Sí | Debe incluir `Z` u offset. |
| `fechaRecuperacion` | ISO 8601 | Sí | Debe incluir zona y ser posterior por al menos un segundo. |
| `duracionSegundos` | integer | Sí | Debe coincidir con las fechas; se tolera una diferencia máxima de un segundo por redondeo. |
| `intentosFallidos` | integer | Sí | Mínimo 1. |
| `detalle` | string | No | Máximo 1000 caracteres; vacío se guarda como `NULL`. |

### Primera recepción — HTTP 201

```json
{
  "idIncidente": "77bb00d6222d46108707d352a9744128",
  "idRegistro": 42,
  "estado": "REGISTRADO"
}
```

### Reintento del mismo incidente — HTTP 200

```json
{
  "idIncidente": "77bb00d6222d46108707d352a9744128",
  "idRegistro": 42,
  "estado": "DUPLICADO"
}
```

El UUID se devuelve normalizado a 32 caracteres, sin guiones. `idRegistro` es
el identificador numérico de la fila del servidor y permanece igual en cada
reintento.

La idempotencia se determina por `(idTerminal, idIncidente)`. No se deduplica
por fecha o servicio: dos caídas distintas podrían comenzar en el mismo segundo.

| HTTP | Acción del terminal |
| --- | --- |
| `201` o `200` | Marcar el incidente local como enviado. |
| `400` | Marcarlo fallido y conservar el detalle para revisión. |
| `429`, `5xx` o timeout | Mantenerlo pendiente y reintentar con el mismo UUID. |

---

## 3. Datos del terminal

```http
GET https://bakeliteapi.sopytec.cl/api/terminal/1
```

### HTTP 200

```json
{
  "idTerminal": 1,
  "nombre": "Torniquete Principal",
  "activo": true
}
```

La base de datos de Bakelite es la fuente autoritativa del nombre. Al iniciar,
el software del torniquete debe consultar este endpoint y actualizar su nombre
local con el valor recibido. No debe enviar el nombre local para sobrescribir
el servidor.

Si el terminal no existe se responde `HTTP 404`; esto se trata como error de
configuración. Un terminal existente pero inactivo se devuelve con
`activo: false` para que el cliente pueda informarlo explícitamente.

---

## Cambios necesarios en el software del torniquete

1. Configurar `API_URL_PING` con
   `https://bakeliteapi.sopytec.cl/api/terminal/health`.
2. Configurar `API_URL_INCIDENTES` con
   `https://bakeliteapi.sopytec.cl/api/terminal/incidents`.
3. Agregar un UUID persistente a cada incidente local y enviarlo como
   `idIncidente`; nunca generar otro UUID al reintentar.
4. Aceptar `idIncidente` (string), `idRegistro` (número) y `estado` en la
   respuesta del incidente.
5. Calcular `duracionSegundos` desde las dos fechas, con la misma precisión en
   segundos que se envía a la API.
6. La sincronización del nombre ya no considera a Bakelite como fuente única.
   Debe implementarse según `CONTRATO_SINCRONIZACION_NOMBRE_TERMINAL.md`.

## Criterio de aceptación en el NUC

La integración se considera terminada cuando se comprueben estos casos:

1. `GET /api/terminal/health` reemplaza cualquier sondeo provisional basado en
   enviar un payload vacío al endpoint de marcas.
2. Con API o red caída, las marcas y los incidentes permanecen en la BD local.
3. Al recuperar conexión, un incidente nuevo devuelve `201 REGISTRADO`.
4. Reenviar el mismo UUID devuelve `200 DUPLICADO` y no crea otra fila.
5. Un `400` queda registrado como fallo definitivo para revisión y no entra en
   un ciclo infinito de reintentos.
6. `429`, `5xx`, timeout o error de red mantienen el registro pendiente.
7. La aceptación de la sincronización bidireccional del nombre se evalúa en
   `CONTRATO_SINCRONIZACION_NOMBRE_TERMINAL.md`.

Este contrato no reemplaza el envío de marcas ni el heartbeat. Para completar
toda la integración del NUC deben aplicarse también:

- `CONTRATO_INTEGRACION_TORNIQUETE.md`;
- `CONTRATO_DISPONIBILIDAD_SOFTWARE_ESCRITORIO.md`.
