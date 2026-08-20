# Contrato de integracion del torniquete

Este documento define el contrato que debe implementar el software Python instalado en el torniquete para enviar marcas de acceso a Bakelite.

## Endpoint

La API oficial de Bakelite está publicada en:

```text
https://bakeliteapi.sopytec.cl/
```

El endpoint completo para enviar marcas es:

```http
POST https://bakeliteapi.sopytec.cl/api/terminal/events
Content-Type: application/json
```

En esta etapa el terminal se identifica mediante `idTerminal`. El terminal principal creado por el sistema tiene actualmente `idTerminal: 1`.

## Identificador unico del evento

Cada marca fisica detectada por el torniquete debe tener un `idEvento` unico. Este identificador no es una clave de seguridad: permite que la API reconozca los reintentos y no guarde dos veces la misma marca.

El software Python debe cumplir obligatoriamente estas reglas:

1. Generar un UUID nuevo cuando se detecta una marca fisica, antes de intentar enviarla.
2. Guardar de forma persistente el `idEvento` y el payload completo en una cola local antes del primer envio. Se recomienda SQLite.
3. Si hay timeout, desconexion, HTTP `429` o HTTP `5xx`, reenviar exactamente el mismo payload con el mismo `idEvento`.
4. No generar otro `idEvento` durante un reintento.
5. No reutilizar un `idEvento` para una marca fisica diferente, aunque pertenezca a la misma persona.
6. No construirlo usando solamente RUT, fecha u hora. Debe generarse con UUID.
7. Considerar entregada la marca tanto con HTTP `201` y estado `REGISTRADO` como con HTTP `200` y estado `DUPLICADO`. Recién entonces se puede quitar de la cola local.

Formato recomendado para Python: UUID v4 hexadecimal de 32 caracteres, en minusculas y sin guiones.

```python
from uuid import uuid4

id_evento = uuid4().hex
# Ejemplo: "f12d8e266da34f3d89c16d634a6e31fe"
```

El `idEvento` debe crearse una sola vez. Este ejemplo muestra el orden obligatorio:

```python
from uuid import uuid4

payload = {
    "idEvento": uuid4().hex,
    "idTerminal": 1,
    "resultado": "AUTORIZADO",
    "rut": "18.419.773-1",
    "nombre": "Nombre Apellido",
    "evento": "ENTRADA",
    "fechaHora": "2026-08-17T14:35:20-04:00",
}

guardar_en_cola_local(payload)  # Debe ocurrir antes de llamar a la API.
enviar_a_api(payload)
```

Si `enviar_a_api` falla, el proceso posterior debe leer `payload` desde la cola local. No debe reconstruirlo ni reemplazar su `idEvento`.

La base de datos genera adicionalmente su propio ID numerico autoincrementable (`IdMarcaAutorizada` o `IdMarcaRechazada`). El software del torniquete no envía ese ID. La API lo devuelve como `idMarca` después de guardar la marca.

## Marca autorizada

```json
{
  "idEvento": "f12d8e266da34f3d89c16d634a6e31fe",
  "idTerminal": 1,
  "resultado": "AUTORIZADO",
  "rut": "18.419.773-1",
  "nombre": "Nombre Apellido",
  "evento": "ENTRADA",
  "fechaHora": "2026-08-17T14:35:20-04:00"
}
```

Para una marca autorizada, `nombre` es obligatorio y `motivoRechazo` debe omitirse.

## Marca rechazada

```json
{
  "idEvento": "78068977f17d4989863dcaf397b857f4",
  "idTerminal": 1,
  "resultado": "RECHAZADO",
  "rut": "9.500.453-9",
  "nombre": "Nombre Apellido",
  "evento": "ENTRADA",
  "fechaHora": "2026-08-17T14:36:02-04:00",
  "motivoRechazo": "Persona sin permiso de acceso"
}
```

Para una marca rechazada, `motivoRechazo` es obligatorio. `nombre` es opcional: debe enviarse cuando el sistema de origen identifica a la persona, aunque no tenga permiso de ingreso. Si el sistema de origen no conoce el nombre, la propiedad puede omitirse o enviarse como `null`.

## Definicion de campos

| Campo | Tipo | Obligatorio | Regla |
| --- | --- | --- | --- |
| `idEvento` | string | Si | UUID creado una sola vez por cada marca; maximo 100 caracteres. |
| `idTerminal` | integer | Si | ID configurado para el torniquete; debe existir y estar activo. |
| `resultado` | string | Si | `AUTORIZADO` o `RECHAZADO`. |
| `rut` | string | Si | RUT chileno valido; puede enviarse con puntos y guion. Acepta `K`. |
| `nombre` | string | Autorizado: si. Rechazado: opcional | Entre 3 y 150 caracteres cuando se informa. |
| `evento` | string | Si | `ENTRADA` o `SALIDA`. |
| `fechaHora` | string ISO 8601 | Si | Fecha y hora real del evento con offset de Chile, por ejemplo `-04:00` o `-03:00`, según corresponda. |
| `motivoRechazo` | string | Solo rechazado | Motivo entregado por la aplicación del torniquete; entre 3 y 250 caracteres. |

La API valida el RUT y lo guarda sin puntos ni guion, con cuerpo de ocho digitos y dígito verificador. Ejemplos: `18.419.773-1` se guarda como `184197731`, `9.500.453-9` como `095004539`, y un RUT terminado en `K` conserva la `K` mayuscula.

## Registro de trabajadores

La API registra un trabajador únicamente al recibir su primera marca `AUTORIZADO`. La marca y el alta del trabajador se guardan dentro de la misma transacción. Si el RUT ya existe en `Trabajadores`, no se crea otro registro. Una marca `RECHAZADO` nunca crea un trabajador, incluso cuando incluye `nombre`.

## Respuestas y reintentos

### HTTP 201: registrado

```json
{
  "idEvento": "f12d8e266da34f3d89c16d634a6e31fe",
  "estado": "REGISTRADO",
  "resultado": "AUTORIZADO",
  "rut": "184197731",
  "nombre": "Nombre Apellido",
  "fechaHora": "2026-08-17T14:35:20-04:00",
  "idMarca": 125
}
```

La marca se guardó correctamente. Se puede quitar de la cola local.

### HTTP 200: duplicado

```json
{
  "idEvento": "f12d8e266da34f3d89c16d634a6e31fe",
  "estado": "DUPLICADO",
  "resultado": "AUTORIZADO",
  "rut": "184197731",
  "nombre": "Nombre Apellido",
  "fechaHora": "2026-08-17T14:35:20-04:00",
  "idMarca": null
}
```

La API ya había recibido ese `(idTerminal, idEvento)`. Debe tratarse como entrega exitosa y quitarse de la cola local.

### HTTP 400: datos invalidos

No se debe reintentar automáticamente el mismo contenido. El software debe conservarlo como fallido y registrar el detalle devuelto por la API para revisión.

### Timeout, error de red, HTTP 429 o HTTP 5xx

La marca debe permanecer en la cola local y reintentarse con el mismo `idEvento`. Se recomienda espera incremental entre intentos y un máximo de espera de 60 segundos, sin eliminar nunca la marca pendiente por un fallo de comunicación.

## Regla de idempotencia

La combinación utilizada por la API es:

```text
(idTerminal, idEvento)
```

Por ello dos terminales pueden generar casualmente el mismo UUID sin interferirse, aunque esa colisión ya es extremadamente improbable. Dentro de un mismo terminal, reutilizar un UUID para otra marca hará que la segunda sea descartada como duplicada.
