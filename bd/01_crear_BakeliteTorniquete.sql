/* ============================================================================
   BakeliteTorniquete - BD local del torniquete (SQL Server, esquema dbo)
   ----------------------------------------------------------------------------
   Flujo que representa:

     1. Se pasa la cedula por la lectora  -> se inserta la fila en dbo.Marcas
        con Rut, Evento (ENTRADA/SALIDA) y FechaHora. Todavia sin respuesta.
     2. Se consulta el RUT a la API EXTERNA -> la peticion y su respuesta
        (rut, habilitado 1/0, nombre, motivo) quedan en dbo.ConsultasApiExterna,
        y el resultado se copia sobre la misma fila de dbo.Marcas.
     3. Se envia la marca a BakeliteApi -> cada intento, con su respuesta o su
        error, queda en dbo.EnviosBakelite, y dbo.Marcas.EstadoEnvio dice si
        ya se subio (ENVIADA), si falta (PENDIENTE) o si fue rechazada (FALLIDA).

   Se puede ejecutar como sa o como userBakelite, y se puede volver a ejecutar
   sin romper nada. Al final deja a userBakelite con acceso TOTAL a esta base.
   ============================================================================ */

SET NOCOUNT ON;
GO

/* ---------------------------------------------------------------------------
   1. Base de datos
      Solo un sysadmin puede crearla. Si el script corre como userBakelite,
      este bloque se salta y se asume que la base ya existe (creada desde
      Navicat o por sa).
   --------------------------------------------------------------------------- */
IF DB_ID(N'BakeliteTorniquete') IS NULL
BEGIN
    IF IS_SRVROLEMEMBER(N'sysadmin') = 1 OR IS_SRVROLEMEMBER(N'dbcreator') = 1
    BEGIN
        PRINT '>> Creando base de datos BakeliteTorniquete...';
        EXEC (N'CREATE DATABASE BakeliteTorniquete;');
    END
    ELSE
        RAISERROR (N'No existe BakeliteTorniquete y este usuario no puede crearla. Crearla primero desde Navicat o como sa.', 20, 1) WITH LOG;
END
GO

USE BakeliteTorniquete;
GO

/* ---------------------------------------------------------------------------
   2. Acceso TOTAL para userBakelite sobre esta base.
      Se ejecuta solo si quien corre el script tiene permiso para otorgarlo
      (sa o cualquier db_owner). Si ya lo tenia, no pasa nada.
   --------------------------------------------------------------------------- */
IF IS_SRVROLEMEMBER(N'sysadmin') = 1 OR IS_ROLEMEMBER(N'db_owner') = 1
BEGIN
    IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = N'userBakelite')
       AND IS_SRVROLEMEMBER(N'sysadmin') = 1
    BEGIN
        PRINT '>> Creando login userBakelite...';
        EXEC (N'CREATE LOGIN userBakelite WITH PASSWORD = ''bakelite123'',
                    DEFAULT_DATABASE = BakeliteTorniquete,
                    CHECK_POLICY = OFF, CHECK_EXPIRATION = OFF;');
    END

    IF EXISTS (SELECT 1 FROM sys.server_principals WHERE name = N'userBakelite')
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'userBakelite')
        BEGIN
            PRINT '>> Dando acceso a userBakelite en BakeliteTorniquete...';
            EXEC (N'CREATE USER userBakelite FOR LOGIN userBakelite;');
        END

        /* db_owner = control total dentro de esta base: leer, escribir, crear
           y borrar objetos, y dar permisos. */
        IF IS_ROLEMEMBER(N'db_owner', N'userBakelite') = 0
            EXEC (N'ALTER ROLE db_owner ADD MEMBER userBakelite;');

        EXEC (N'GRANT CONTROL ON SCHEMA::dbo TO userBakelite;');
    END
END
ELSE
    PRINT '>> Sin permisos para otorgar accesos: se omite ese paso.';
GO

/* ---------------------------------------------------------------------------
   3. Terminal
      La primera fila usa el mismo IdTerminal que exige BakeliteApi (1).
      El nombre se puede cambiar desde la app.
   --------------------------------------------------------------------------- */
IF OBJECT_ID(N'dbo.Terminales', N'U') IS NULL
CREATE TABLE dbo.Terminales (
    IdTerminal        INT           NOT NULL CONSTRAINT PK_Terminales PRIMARY KEY,
    Nombre            NVARCHAR(150) NOT NULL,
    Ubicacion         NVARCHAR(200) NULL,
    Activo            BIT           NOT NULL CONSTRAINT DF_Terminales_Activo DEFAULT (1),
    FechaCreacion     DATETIME2(0)  NOT NULL CONSTRAINT DF_Terminales_FCrea DEFAULT (SYSDATETIME()),
    FechaModificacion DATETIME2(0)  NULL,
    ModificadoPor     NVARCHAR(100) NULL,
    CONSTRAINT CK_Terminales_Nombre CHECK (LEN(LTRIM(RTRIM(Nombre))) > 0)
);
GO

IF NOT EXISTS (SELECT 1 FROM dbo.Terminales WHERE IdTerminal = 1)
    INSERT dbo.Terminales (IdTerminal, Nombre) VALUES (1, N'Terminal 1');
GO

/* ---------------------------------------------------------------------------
   4. Versiones de la aplicacion
      Pueden cargarse varias, pero solo UNA con Activo = 1. El indice filtrado
      unico lo garantiza a nivel de motor.
   --------------------------------------------------------------------------- */
IF OBJECT_ID(N'dbo.Versiones', N'U') IS NULL
CREATE TABLE dbo.Versiones (
    IdVersion   INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_Versiones PRIMARY KEY,
    Numero      VARCHAR(30)   NOT NULL,
    SubidoPor   NVARCHAR(100) NOT NULL,
    FechaSubida DATETIME2(0)  NOT NULL CONSTRAINT DF_Versiones_Fecha DEFAULT (SYSDATETIME()),
    Notas       NVARCHAR(500) NULL,
    Activo      BIT           NOT NULL CONSTRAINT DF_Versiones_Activo DEFAULT (0),
    CONSTRAINT UQ_Versiones_Numero UNIQUE (Numero)
);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'UX_Versiones_UnaActiva')
    CREATE UNIQUE INDEX UX_Versiones_UnaActiva ON dbo.Versiones (Activo) WHERE Activo = 1;
GO

IF NOT EXISTS (SELECT 1 FROM dbo.Versiones)
    INSERT dbo.Versiones (Numero, SubidoPor, Notas, Activo)
    VALUES ('1.0.0', N'instalacion', N'Version inicial del torniquete.', 1);
GO

/* ---------------------------------------------------------------------------
   5. Trabajadores
      RUT normalizado (sin puntos ni guion, DV en mayuscula). Se conoce a la
      persona recien cuando la API externa devuelve su nombre.
   --------------------------------------------------------------------------- */
IF OBJECT_ID(N'dbo.Trabajadores', N'U') IS NULL
CREATE TABLE dbo.Trabajadores (
    IdTrabajador     INT IDENTITY(1,1) NOT NULL CONSTRAINT PK_Trabajadores PRIMARY KEY,
    Rut              VARCHAR(12)   NOT NULL,
    RutFormateado    VARCHAR(15)   NULL,
    Nombre           NVARCHAR(150) NULL,
    Habilitado       BIT           NULL,      -- ultimo estado informado por la API externa
    FechaAlta        DATETIME2(0)  NOT NULL CONSTRAINT DF_Trab_Alta DEFAULT (SYSDATETIME()),
    FechaUltimaMarca DATETIME2(0)  NULL,
    CONSTRAINT UQ_Trabajadores_Rut UNIQUE (Rut)
);
GO

/* ---------------------------------------------------------------------------
   6. Marcas
      Una fila por pasada de cedula. Nace en el momento de la lectura (paso 1)
      y se va completando con la respuesta de la API externa (paso 2) y con el
      estado del envio a BakeliteApi (paso 3).
   --------------------------------------------------------------------------- */
IF OBJECT_ID(N'dbo.Marcas', N'U') IS NULL
CREATE TABLE dbo.Marcas (
    IdMarca       BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_Marcas PRIMARY KEY,
    IdEvento      VARCHAR(100)  NOT NULL,     -- UUID del contrato, creado una sola vez
    IdTerminal    INT           NOT NULL,
    IdTrabajador  INT           NULL,

    /* --- Paso 1: lo que se sabe al pasar la cedula --- */
    Rut           VARCHAR(12)   NOT NULL,
    RutFormateado VARCHAR(15)   NULL,
    Evento        VARCHAR(10)   NOT NULL,     -- ENTRADA / SALIDA
    FechaHora     DATETIMEOFFSET(0) NOT NULL, -- momento real de la lectura
    Ubicacion     NVARCHAR(200) NULL,

    /* --- Paso 2: respuesta de la API externa --- */
    Habilitado    BIT           NULL,         -- 1 habilitado, 0 rechazado, NULL sin respuesta
    Nombre        NVARCHAR(150) NULL,         -- puede venir o no
    Motivo        NVARCHAR(250) NULL,         -- por que fue rechazado
    Resultado     VARCHAR(20)   NULL,         -- AUTORIZADO / RECHAZADO / SIN_RESPUESTA
    FechaConsulta DATETIME2(0)  NULL,

    /* --- Paso 3: envio a BakeliteApi --- */
    PayloadJson   NVARCHAR(MAX) NULL,         -- lo que se envia, textual (no se reconstruye)
    EstadoEnvio   VARCHAR(20)   NOT NULL CONSTRAINT DF_Marcas_Estado DEFAULT ('PENDIENTE'),
    Intentos      INT           NOT NULL CONSTRAINT DF_Marcas_Intentos DEFAULT (0),
    UltimoIntento DATETIME2(0)  NULL,
    FechaEnvio    DATETIME2(0)  NULL,
    IdMarcaApi    BIGINT        NULL,         -- idMarca que devuelve BakeliteApi
    EstadoApi     VARCHAR(20)   NULL,         -- REGISTRADO / DUPLICADO
    UltimoError   NVARCHAR(500) NULL,

    FechaRegistro DATETIME2(0)  NOT NULL CONSTRAINT DF_Marcas_FReg DEFAULT (SYSDATETIME()),
    IdVersion     INT           NULL,

    CONSTRAINT UQ_Marcas_Idempotencia UNIQUE (IdTerminal, IdEvento),
    CONSTRAINT FK_Marcas_Terminal   FOREIGN KEY (IdTerminal)   REFERENCES dbo.Terminales (IdTerminal),
    CONSTRAINT FK_Marcas_Trabajador FOREIGN KEY (IdTrabajador) REFERENCES dbo.Trabajadores (IdTrabajador),
    CONSTRAINT FK_Marcas_Version    FOREIGN KEY (IdVersion)    REFERENCES dbo.Versiones (IdVersion),
    CONSTRAINT CK_Marcas_Evento     CHECK (Evento IN ('ENTRADA','SALIDA')),
    CONSTRAINT CK_Marcas_Resultado  CHECK (Resultado IS NULL OR Resultado IN ('AUTORIZADO','RECHAZADO','SIN_RESPUESTA')),
    CONSTRAINT CK_Marcas_EstadoEnvio CHECK (EstadoEnvio IN ('NO_APLICA','PENDIENTE','ENVIADA','FALLIDA'))
);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Marcas_Pendientes')
    CREATE INDEX IX_Marcas_Pendientes ON dbo.Marcas (IdMarca) WHERE EstadoEnvio = 'PENDIENTE';
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Marcas_Rut')
    CREATE INDEX IX_Marcas_Rut ON dbo.Marcas (Rut, FechaHora DESC);
GO

/* ---------------------------------------------------------------------------
   7. ConsultasApiExterna
      Que se le pregunto a la API externa y que respondio, tal cual. Una marca
      puede tener varias consultas si hubo reintentos.
   --------------------------------------------------------------------------- */
IF OBJECT_ID(N'dbo.ConsultasApiExterna', N'U') IS NULL
CREATE TABLE dbo.ConsultasApiExterna (
    IdConsulta    BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_ConsultasApiExterna PRIMARY KEY,
    IdMarca       BIGINT         NULL,        -- NULL si la consulta no llego a ser marca
    RutConsultado VARCHAR(12)    NOT NULL,    -- lo que se envio
    Url           NVARCHAR(300)  NULL,

    /* --- Respuesta --- */
    HttpStatus    INT            NULL,        -- NULL = no hubo respuesta (red caida)
    RutRespuesta  VARCHAR(12)    NULL,        -- el rut que devolvio
    Habilitado    BIT            NULL,        -- 1 habilitado, 0 rechazado
    Nombre        NVARCHAR(150)  NULL,        -- viene solo si esta registrado
    Motivo        NVARCHAR(250)  NULL,        -- viene cuando no esta habilitado/registrado
    RespuestaJson NVARCHAR(MAX)  NULL,        -- cuerpo completo, para auditar

    Exito         BIT            NOT NULL CONSTRAINT DF_Consultas_Exito DEFAULT (0),
    MensajeError  NVARCHAR(1000) NULL,
    DuracionMs    INT            NULL,
    FechaHora     DATETIME2(0)   NOT NULL CONSTRAINT DF_Consultas_Fecha DEFAULT (SYSDATETIME()),
    CONSTRAINT FK_Consultas_Marca FOREIGN KEY (IdMarca) REFERENCES dbo.Marcas (IdMarca)
);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Consultas_Marca')
    CREATE INDEX IX_Consultas_Marca ON dbo.ConsultasApiExterna (IdMarca);
GO

/* ---------------------------------------------------------------------------
   8. EnviosBakelite
      UNA fila por marca, que se va actualizando. No se guarda una fila por
      intento: se conserva el primer intento, el ultimo, cuantos hubo, el
      ultimo error, y los datos del envio exitoso cuando ocurre.
   --------------------------------------------------------------------------- */
IF OBJECT_ID(N'dbo.EnviosBakelite', N'U') IS NOT NULL
   AND COL_LENGTH(N'dbo.EnviosBakelite', N'NumeroIntento') IS NOT NULL
BEGIN
    /* Version anterior (una fila por intento): se reemplaza. Solo contenia el
       historial de intentos; el estado real vive en dbo.Marcas. */
    PRINT '>> Reemplazando dbo.EnviosBakelite por la version de una fila por marca...';
    DROP TABLE dbo.EnviosBakelite;
END
GO

IF OBJECT_ID(N'dbo.EnviosBakelite', N'U') IS NULL
CREATE TABLE dbo.EnviosBakelite (
    IdEnvio          BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_EnviosBakelite PRIMARY KEY,
    IdMarca          BIGINT         NOT NULL,
    Url              NVARCHAR(300)  NULL,
    RequestJson      NVARCHAR(MAX)  NULL,      -- lo que se envia (no cambia entre intentos)

    /* --- Conteo de intentos --- */
    Intentos         INT            NOT NULL CONSTRAINT DF_Envios_Intentos DEFAULT (0),
    PrimerIntento    DATETIME2(0)   NULL,
    UltimoIntento    DATETIME2(0)   NULL,

    /* --- Ultimo intento (exitoso o no) --- */
    UltimoHttpStatus INT            NULL,      -- NULL = no hubo respuesta
    UltimaRespuesta  NVARCHAR(MAX)  NULL,
    UltimoError      NVARCHAR(1000) NULL,
    UltimaDuracionMs INT            NULL,

    /* --- El envio exitoso, cuando ocurre --- */
    Exito            BIT            NOT NULL CONSTRAINT DF_Envios_Exito DEFAULT (0),
    FechaExito       DATETIME2(0)   NULL,
    HttpStatusExito  INT            NULL,      -- 201 REGISTRADO / 200 DUPLICADO
    EstadoApi        VARCHAR(20)    NULL,
    IdMarcaApi       BIGINT         NULL,

    CONSTRAINT UQ_Envios_Marca UNIQUE (IdMarca),
    CONSTRAINT FK_Envios_Marca FOREIGN KEY (IdMarca) REFERENCES dbo.Marcas (IdMarca)
);
GO

/* ---------------------------------------------------------------------------
   9. EstadoServicios
      Una fila por servicio externo. Sirve para que la pantalla pueda mostrar
      "sin conexion" y "ultima conexion: hh:mm" incluso recien reiniciada la app.
        BAKELITE -> https://bakeliteapi.sopytec.cl (envio de marcas)
        EXTERNA  -> API que responde si un RUT esta habilitado
   --------------------------------------------------------------------------- */
IF OBJECT_ID(N'dbo.EstadoServicios', N'U') IS NULL
CREATE TABLE dbo.EstadoServicios (
    Servicio          VARCHAR(20)    NOT NULL CONSTRAINT PK_EstadoServicios PRIMARY KEY,
    Descripcion       NVARCHAR(200)  NULL,
    EnLinea           BIT            NOT NULL CONSTRAINT DF_Estado_EnLinea DEFAULT (0),
    UltimaConexionOk  DATETIME2(0)   NULL,
    UltimaFalla       DATETIME2(0)   NULL,
    UltimoError       NVARCHAR(1000) NULL,
    FechaActualizacion DATETIME2(0)  NOT NULL CONSTRAINT DF_Estado_Fecha DEFAULT (SYSDATETIME()),
    CONSTRAINT CK_EstadoServicios CHECK (Servicio IN ('BAKELITE','EXTERNA'))
);
GO

MERGE dbo.EstadoServicios AS d
USING (VALUES ('BAKELITE', N'API de Bakelite: recibe las marcas.'),
              ('EXTERNA',  N'API externa: responde si el RUT esta habilitado.'))
      AS o(Servicio, Descripcion)
    ON d.Servicio = o.Servicio
WHEN NOT MATCHED THEN INSERT (Servicio, Descripcion) VALUES (o.Servicio, o.Descripcion);
GO

/* ---------------------------------------------------------------------------
   10. IncidentesConexion
       Un corte de conexion = UNA fila. Se abre cuando se detecta la caida y se
       cierra cuando el servicio vuelve. Mientras esta abierta, FechaRecuperacion
       es NULL. EstadoEnvio sirve para avisarle despues a BakeliteApi que hubo un
       corte entre las XX y las XX.
   --------------------------------------------------------------------------- */
IF OBJECT_ID(N'dbo.IncidentesConexion', N'U') IS NULL
CREATE TABLE dbo.IncidentesConexion (
    IdIncidente       BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_IncidentesConexion PRIMARY KEY,
    IdIncidenteUuid   VARCHAR(32)    NULL,       -- idIncidente del contrato (UUID sin guiones)
    IdTerminal        INT            NOT NULL,
    Servicio          VARCHAR(20)    NOT NULL,   -- BAKELITE / EXTERNA
    FechaDeteccion    DATETIMEOFFSET(0) NOT NULL,
    FechaRecuperacion DATETIMEOFFSET(0) NULL,    -- NULL = sigue caido
    DuracionSegundos  AS (CASE WHEN FechaRecuperacion IS NULL THEN NULL
                              ELSE DATEDIFF(SECOND, FechaDeteccion, FechaRecuperacion) END),
    IntentosFallidos  INT            NOT NULL CONSTRAINT DF_Incidentes_Intentos DEFAULT (1),
    PrimerError       NVARCHAR(1000) NULL,
    UltimoError       NVARCHAR(1000) NULL,

    /* Aviso del corte a BakeliteApi (solo aplica al reconectar). */
    EstadoEnvio       VARCHAR(20)    NOT NULL CONSTRAINT DF_Incidentes_Estado DEFAULT ('PENDIENTE'),
    IntentosEnvio     INT            NOT NULL CONSTRAINT DF_Incidentes_IntEnvio DEFAULT (0),
    UltimoIntentoEnvio DATETIME2(0)  NULL,
    FechaEnvio        DATETIME2(0)   NULL,
    HttpStatusEnvio   INT            NULL,
    RespuestaEnvio    NVARCHAR(MAX)  NULL,
    ErrorEnvio        NVARCHAR(1000) NULL,
    IdRegistroApi     BIGINT         NULL,       -- idRegistro que devuelve la API
    EstadoApi         VARCHAR(20)    NULL,       -- REGISTRADO / DUPLICADO

    CONSTRAINT FK_Incidentes_Terminal FOREIGN KEY (IdTerminal) REFERENCES dbo.Terminales (IdTerminal),
    CONSTRAINT CK_Incidentes_Servicio CHECK (Servicio IN ('BAKELITE','EXTERNA')),
    CONSTRAINT CK_Incidentes_Estado   CHECK (EstadoEnvio IN ('PENDIENTE','ENVIADO','FALLIDO','NO_APLICA'))
);
GO

/* --- Contrato de incidentes: identificador propio del terminal ---
   La API deduplica por (idTerminal, idIncidente), y ese idIncidente es un UUID
   que crea el terminal y reutiliza en todos los reintentos. El IDENTITY de
   arriba no sirve para eso: es local y podria repetirse entre equipos. */
IF COL_LENGTH(N'dbo.IncidentesConexion', N'IdIncidenteUuid') IS NULL
    ALTER TABLE dbo.IncidentesConexion ADD IdIncidenteUuid VARCHAR(32) NULL;
GO

/* Las filas que existan de antes reciben su UUID para no quedar sin clave. */
UPDATE dbo.IncidentesConexion
   SET IdIncidenteUuid = REPLACE(CONVERT(VARCHAR(36), NEWID()), '-', '')
 WHERE IdIncidenteUuid IS NULL;
GO

/* idRegistro y estado que devuelve la API al aceptar el aviso. */
IF COL_LENGTH(N'dbo.IncidentesConexion', N'IdRegistroApi') IS NULL
    ALTER TABLE dbo.IncidentesConexion ADD IdRegistroApi BIGINT NULL;
GO

IF COL_LENGTH(N'dbo.IncidentesConexion', N'EstadoApi') IS NULL
    ALTER TABLE dbo.IncidentesConexion ADD EstadoApi VARCHAR(20) NULL;   -- REGISTRADO / DUPLICADO
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'UX_Incidentes_Uuid')
    CREATE UNIQUE INDEX UX_Incidentes_Uuid
        ON dbo.IncidentesConexion (IdTerminal, IdIncidenteUuid)
        WHERE IdIncidenteUuid IS NOT NULL;
GO

/* Solo puede haber UN incidente abierto por servicio: el indice filtrado lo
   garantiza, asi no se duplican cortes por reintentos seguidos. */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'UX_Incidentes_Abierto')
    CREATE UNIQUE INDEX UX_Incidentes_Abierto
        ON dbo.IncidentesConexion (Servicio) WHERE FechaRecuperacion IS NULL;
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Incidentes_Pendientes')
    CREATE INDEX IX_Incidentes_Pendientes ON dbo.IncidentesConexion (IdIncidente)
        WHERE EstadoEnvio = 'PENDIENTE' AND FechaRecuperacion IS NOT NULL;
GO

/* ---------------------------------------------------------------------------
   11. Errores del sistema (lectora, arduino, api, bd, app).
   --------------------------------------------------------------------------- */
IF OBJECT_ID(N'dbo.Errores', N'U') IS NULL
CREATE TABLE dbo.Errores (
    IdError    BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_Errores PRIMARY KEY,
    IdTerminal INT            NULL,
    Origen     NVARCHAR(60)   NOT NULL,
    Nivel      VARCHAR(10)    NOT NULL CONSTRAINT DF_Errores_Nivel DEFAULT ('ERROR'),
    Mensaje    NVARCHAR(1000) NOT NULL,
    Detalle    NVARCHAR(MAX)  NULL,
    IdMarca    BIGINT         NULL,
    FechaHora  DATETIME2(0)   NOT NULL CONSTRAINT DF_Errores_Fecha DEFAULT (SYSDATETIME()),
    CONSTRAINT FK_Errores_Terminal FOREIGN KEY (IdTerminal) REFERENCES dbo.Terminales (IdTerminal),
    CONSTRAINT FK_Errores_Marca    FOREIGN KEY (IdMarca)    REFERENCES dbo.Marcas (IdMarca),
    CONSTRAINT CK_Errores_Nivel CHECK (Nivel IN ('INFO','WARN','ERROR','CRITICO'))
);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Errores_Fecha')
    CREATE INDEX IX_Errores_Fecha ON dbo.Errores (FechaHora DESC);
GO

PRINT '';
PRINT '===============================================================';
PRINT ' BakeliteTorniquete lista (dbo). userBakelite con acceso total.';
PRINT '===============================================================';
GO

SELECT Terminal = Nombre, IdTerminal FROM dbo.Terminales;
SELECT VersionActiva = Numero, SubidoPor, FechaSubida FROM dbo.Versiones WHERE Activo = 1;
GO
