/* ===========================================================================
   BakeliteTorniquete — creacion completa desde cero
   ---------------------------------------------------------------------------
   Ejecutar con la cuenta 'sa' (o cualquiera con permisos de servidor).

       sqlcmd -S localhost -U sa -P <clave_sa> -i crear_bd_completa.sql

   Crea, en este orden:
     1. la base de datos
     2. el login y el usuario userBakelite, con sus permisos
     3. todas las tablas, claves, restricciones e indices
     4. las filas minimas sin las cuales la aplicacion no arranca

   Es idempotente: correrlo dos veces no rompe nada ni duplica datos.
   NO trae datos historicos: marcas, errores y consultas quedan vacios.
   =========================================================================== */

/* ---------------------------------------------------------------------------
   1. Base de datos
   --------------------------------------------------------------------------- */
IF DB_ID(N'BakeliteTorniquete') IS NULL
BEGIN
    CREATE DATABASE BakeliteTorniquete;
    PRINT '>> Base BakeliteTorniquete creada.';
END
ELSE
    PRINT '>> La base BakeliteTorniquete ya existia.';
GO

/* ---------------------------------------------------------------------------
   2. Login y usuario de la aplicacion
      La app se conecta con autenticacion SQL, no de Windows: el NUC es Linux.
      La clave debe coincidir con config.SQL_CLAVE (o con la variable de
      entorno BAKELITE_SQL_CLAVE).
   --------------------------------------------------------------------------- */
IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = N'userBakelite')
BEGIN
    CREATE LOGIN userBakelite
        WITH PASSWORD = 'bakelite123',
             DEFAULT_DATABASE = BakeliteTorniquete,
             CHECK_EXPIRATION = OFF,
             CHECK_POLICY = OFF;
    PRINT '>> Login userBakelite creado.';
END
ELSE
    PRINT '>> El login userBakelite ya existia.';
GO

USE BakeliteTorniquete;
GO

IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'userBakelite')
BEGIN
    CREATE USER userBakelite FOR LOGIN userBakelite;
    PRINT '>> Usuario userBakelite creado en la base.';
END
GO

/* Permisos: la aplicacion lee y escribe datos, pero NUNCA cambia la estructura.
   Las migraciones se ejecutan con sa, a proposito: asi un fallo del software no
   puede alterar el esquema. */
ALTER ROLE db_datareader ADD MEMBER userBakelite;
ALTER ROLE db_datawriter ADD MEMBER userBakelite;
GO

/* Necesario para las columnas IDENTITY con OUTPUT INSERTED y para leer los
   metadatos de las tablas. */
GRANT VIEW DEFINITION ON SCHEMA::dbo TO userBakelite;
GO

PRINT '>> Permisos otorgados a userBakelite (lectura y escritura de datos).';
GO

/* ---------------------------------------------------------------------------
   3. Tablas
   --------------------------------------------------------------------------- */
IF OBJECT_ID(N'dbo.Terminales', N'U') IS NULL
CREATE TABLE dbo.Terminales (
    IdTerminal           INT                NOT NULL,
    Nombre               NVARCHAR(150)      NOT NULL,
    Ubicacion            NVARCHAR(200)      NULL,
    Activo               BIT                NOT NULL
        CONSTRAINT DF_Terminales_Activo DEFAULT ((1)),
    FechaCreacion        DATETIME2(0)       NOT NULL
        CONSTRAINT DF_Terminales_FCrea DEFAULT (sysdatetime()),
    FechaModificacion    DATETIME2(0)       NULL,
    ModificadoPor        NVARCHAR(100)      NULL,
    NombreFecha          DATETIMEOFFSET(0)  NOT NULL
        CONSTRAINT DF_Terminales_NombreFecha DEFAULT (sysdatetimeoffset()),
    NombreOrigen         VARCHAR(10)        NOT NULL
        CONSTRAINT DF_Terminales_NombreOrigen DEFAULT ('LOCAL'),
    NombrePor            NVARCHAR(100)      NULL,
    NombreSincronizado   BIT                NOT NULL
        CONSTRAINT DF_Terminales_NombreSync DEFAULT ((1)),
    ArduinoConectado     BIT                NULL,
    ArduinoPuerto        NVARCHAR(100)      NULL,
    ArduinoEstadoFecha   DATETIMEOFFSET(0)  NULL,
    CONSTRAINT PK_Terminales PRIMARY KEY (IdTerminal),
    CONSTRAINT CK_Terminales_Nombre CHECK (len(ltrim(rtrim([Nombre])))>(0)),
    CONSTRAINT CK_Terminales_NombreOrigen CHECK ([NombreOrigen]='API' OR [NombreOrigen]='LOCAL')
);
GO

IF OBJECT_ID(N'dbo.Versiones', N'U') IS NULL
CREATE TABLE dbo.Versiones (
    IdVersion            INT                IDENTITY(1,1) NOT NULL,
    Numero               VARCHAR(30)        NOT NULL,
    SubidoPor            NVARCHAR(100)      NOT NULL,
    FechaSubida          DATETIME2(0)       NOT NULL
        CONSTRAINT DF_Versiones_Fecha DEFAULT (sysdatetime()),
    Notas                NVARCHAR(500)      NULL,
    Activo               BIT                NOT NULL
        CONSTRAINT DF_Versiones_Activo DEFAULT ((0)),
    CONSTRAINT PK_Versiones PRIMARY KEY (IdVersion)
);
GO

IF OBJECT_ID(N'dbo.Trabajadores', N'U') IS NULL
CREATE TABLE dbo.Trabajadores (
    IdTrabajador         INT                IDENTITY(1,1) NOT NULL,
    Rut                  VARCHAR(12)        NOT NULL,
    RutFormateado        VARCHAR(15)        NULL,
    Nombre               NVARCHAR(150)      NULL,
    Habilitado           BIT                NULL,
    FechaAlta            DATETIME2(0)       NOT NULL
        CONSTRAINT DF_Trab_Alta DEFAULT (sysdatetime()),
    FechaUltimaMarca     DATETIME2(0)       NULL,
    CONSTRAINT PK_Trabajadores PRIMARY KEY (IdTrabajador)
);
GO

IF OBJECT_ID(N'dbo.Marcas', N'U') IS NULL
CREATE TABLE dbo.Marcas (
    IdMarca              BIGINT             IDENTITY(1,1) NOT NULL,
    IdEvento             VARCHAR(100)       NOT NULL,
    IdTerminal           INT                NOT NULL,
    IdTrabajador         INT                NULL,
    Rut                  VARCHAR(12)        NOT NULL,
    RutFormateado        VARCHAR(15)        NULL,
    Evento               VARCHAR(10)        NOT NULL,
    FechaHora            DATETIMEOFFSET(0)  NOT NULL,
    Ubicacion            NVARCHAR(200)      NULL,
    Habilitado           BIT                NULL,
    Nombre               NVARCHAR(150)      NULL,
    Motivo               NVARCHAR(250)      NULL,
    Resultado            VARCHAR(20)        NULL,
    FechaConsulta        DATETIME2(0)       NULL,
    PayloadJson          NVARCHAR(MAX)      NULL,
    EstadoEnvio          VARCHAR(20)        NOT NULL
        CONSTRAINT DF_Marcas_Estado DEFAULT ('PENDIENTE'),
    Intentos             INT                NOT NULL
        CONSTRAINT DF_Marcas_Intentos DEFAULT ((0)),
    UltimoIntento        DATETIME2(0)       NULL,
    FechaEnvio           DATETIME2(0)       NULL,
    IdMarcaApi           BIGINT             NULL,
    EstadoApi            VARCHAR(20)        NULL,
    UltimoError          NVARCHAR(500)      NULL,
    FechaRegistro        DATETIME2(0)       NOT NULL
        CONSTRAINT DF_Marcas_FReg DEFAULT (sysdatetime()),
    IdVersion            INT                NULL,
    CONSTRAINT PK_Marcas PRIMARY KEY (IdMarca),
    CONSTRAINT CK_Marcas_EstadoEnvio CHECK ([EstadoEnvio]='FALLIDA' OR [EstadoEnvio]='ENVIADA' OR [EstadoEnvio]='PENDIENTE' OR [EstadoEnvio]='NO_APLICA'),
    CONSTRAINT CK_Marcas_Evento CHECK ([Evento]='SALIDA' OR [Evento]='ENTRADA'),
    CONSTRAINT CK_Marcas_Resultado CHECK ([Resultado] IS NULL OR ([Resultado]='SIN_RESPUESTA' OR [Resultado]='RECHAZADO' OR [Resultado]='AUTORIZADO'))
);
GO

IF OBJECT_ID(N'dbo.ConsultasApiExterna', N'U') IS NULL
CREATE TABLE dbo.ConsultasApiExterna (
    IdConsulta           BIGINT             IDENTITY(1,1) NOT NULL,
    IdMarca              BIGINT             NULL,
    RutConsultado        VARCHAR(12)        NOT NULL,
    Url                  NVARCHAR(300)      NULL,
    HttpStatus           INT                NULL,
    RutRespuesta         VARCHAR(12)        NULL,
    Habilitado           BIT                NULL,
    Nombre               NVARCHAR(150)      NULL,
    Motivo               NVARCHAR(250)      NULL,
    RespuestaJson        NVARCHAR(MAX)      NULL,
    Exito                BIT                NOT NULL
        CONSTRAINT DF_Consultas_Exito DEFAULT ((0)),
    MensajeError         NVARCHAR(1000)     NULL,
    DuracionMs           INT                NULL,
    FechaHora            DATETIME2(0)       NOT NULL
        CONSTRAINT DF_Consultas_Fecha DEFAULT (sysdatetime()),
    CONSTRAINT PK_ConsultasApiExterna PRIMARY KEY (IdConsulta)
);
GO

IF OBJECT_ID(N'dbo.EnviosBakelite', N'U') IS NULL
CREATE TABLE dbo.EnviosBakelite (
    IdEnvio              BIGINT             IDENTITY(1,1) NOT NULL,
    IdMarca              BIGINT             NOT NULL,
    Url                  NVARCHAR(300)      NULL,
    RequestJson          NVARCHAR(MAX)      NULL,
    Intentos             INT                NOT NULL
        CONSTRAINT DF_Envios_Intentos DEFAULT ((0)),
    PrimerIntento        DATETIME2(0)       NULL,
    UltimoIntento        DATETIME2(0)       NULL,
    UltimoHttpStatus     INT                NULL,
    UltimaRespuesta      NVARCHAR(MAX)      NULL,
    UltimoError          NVARCHAR(1000)     NULL,
    UltimaDuracionMs     INT                NULL,
    Exito                BIT                NOT NULL
        CONSTRAINT DF_Envios_Exito DEFAULT ((0)),
    FechaExito           DATETIME2(0)       NULL,
    HttpStatusExito      INT                NULL,
    EstadoApi            VARCHAR(20)        NULL,
    IdMarcaApi           BIGINT             NULL,
    CONSTRAINT PK_EnviosBakelite PRIMARY KEY (IdEnvio)
);
GO

IF OBJECT_ID(N'dbo.Errores', N'U') IS NULL
CREATE TABLE dbo.Errores (
    IdError              BIGINT             IDENTITY(1,1) NOT NULL,
    IdTerminal           INT                NULL,
    Origen               NVARCHAR(60)       NOT NULL,
    Nivel                VARCHAR(10)        NOT NULL
        CONSTRAINT DF_Errores_Nivel DEFAULT ('ERROR'),
    Mensaje              NVARCHAR(1000)     NOT NULL,
    Detalle              NVARCHAR(MAX)      NULL,
    IdMarca              BIGINT             NULL,
    FechaHora            DATETIME2(0)       NOT NULL
        CONSTRAINT DF_Errores_Fecha DEFAULT (sysdatetime()),
    CONSTRAINT PK_Errores PRIMARY KEY (IdError),
    CONSTRAINT CK_Errores_Nivel CHECK ([Nivel]='CRITICO' OR [Nivel]='ERROR' OR [Nivel]='WARN' OR [Nivel]='INFO')
);
GO

IF OBJECT_ID(N'dbo.EstadoServicios', N'U') IS NULL
CREATE TABLE dbo.EstadoServicios (
    Servicio             VARCHAR(20)        NOT NULL,
    Descripcion          NVARCHAR(200)      NULL,
    EnLinea              BIT                NOT NULL
        CONSTRAINT DF_Estado_EnLinea DEFAULT ((0)),
    UltimaConexionOk     DATETIME2(0)       NULL,
    UltimaFalla          DATETIME2(0)       NULL,
    UltimoError          NVARCHAR(1000)     NULL,
    FechaActualizacion   DATETIME2(0)       NOT NULL
        CONSTRAINT DF_Estado_Fecha DEFAULT (sysdatetime()),
    CONSTRAINT PK_EstadoServicios PRIMARY KEY (Servicio),
    CONSTRAINT CK_EstadoServicios CHECK ([Servicio]='EXTERNA' OR [Servicio]='BAKELITE')
);
GO

IF OBJECT_ID(N'dbo.IncidentesConexion', N'U') IS NULL
CREATE TABLE dbo.IncidentesConexion (
    IdIncidente          BIGINT             IDENTITY(1,1) NOT NULL,
    IdTerminal           INT                NOT NULL,
    Servicio             VARCHAR(20)        NOT NULL,
    FechaDeteccion       DATETIMEOFFSET(0)  NOT NULL,
    FechaRecuperacion    DATETIMEOFFSET(0)  NULL,
    DuracionSegundos     AS (case when [FechaRecuperacion] IS NULL then NULL else datediff(second,[FechaDeteccion],[FechaRecuperacion]) end),
    IntentosFallidos     INT                NOT NULL
        CONSTRAINT DF_Incidentes_Intentos DEFAULT ((1)),
    PrimerError          NVARCHAR(1000)     NULL,
    UltimoError          NVARCHAR(1000)     NULL,
    EstadoEnvio          VARCHAR(20)        NOT NULL
        CONSTRAINT DF_Incidentes_Estado DEFAULT ('PENDIENTE'),
    IntentosEnvio        INT                NOT NULL
        CONSTRAINT DF_Incidentes_IntEnvio DEFAULT ((0)),
    UltimoIntentoEnvio   DATETIME2(0)       NULL,
    FechaEnvio           DATETIME2(0)       NULL,
    HttpStatusEnvio      INT                NULL,
    RespuestaEnvio       NVARCHAR(MAX)      NULL,
    ErrorEnvio           NVARCHAR(1000)     NULL,
    IdIncidenteUuid      VARCHAR(32)        NULL,
    IdRegistroApi        BIGINT             NULL,
    EstadoApi            VARCHAR(20)        NULL,
    CONSTRAINT PK_IncidentesConexion PRIMARY KEY (IdIncidente),
    CONSTRAINT CK_Incidentes_Estado CHECK ([EstadoEnvio]='NO_APLICA' OR [EstadoEnvio]='FALLIDO' OR [EstadoEnvio]='ENVIADO' OR [EstadoEnvio]='PENDIENTE'),
    CONSTRAINT CK_Incidentes_Servicio CHECK ([Servicio]='EXTERNA' OR [Servicio]='BAKELITE')
);
GO

IF OBJECT_ID(N'dbo.Lectoras', N'U') IS NULL
CREATE TABLE dbo.Lectoras (
    Numero               INT                NOT NULL,
    Sentido              CHAR(1)            NOT NULL,
    Descripcion          NVARCHAR(150)      NULL,
    UltimoPuerto         NVARCHAR(100)      NULL,
    FechaModificacion    DATETIME2(0)       NULL,
    ModificadoPor        NVARCHAR(100)      NULL,
    IdTerminal           INT                NOT NULL,
    Activa               BIT                NOT NULL
        CONSTRAINT DF_Lectoras_Activa DEFAULT ((1)),
    ConfigFecha          DATETIMEOFFSET(0)  NOT NULL
        CONSTRAINT DF_Lectoras_ConfigFecha DEFAULT (sysdatetimeoffset()),
    ConfigOrigen         VARCHAR(10)        NOT NULL
        CONSTRAINT DF_Lectoras_ConfigOrigen DEFAULT ('LOCAL'),
    ConfigPor            NVARCHAR(100)      NULL,
    Sincronizado         BIT                NOT NULL
        CONSTRAINT DF_Lectoras_Sync DEFAULT ((1)),
    Conectada            BIT                NULL,
    UltimaLectura        DATETIMEOFFSET(0)  NULL,
    UltimoError          NVARCHAR(500)      NULL,
    EstadoFecha          DATETIMEOFFSET(0)  NULL,
    Ancla                NVARCHAR(200)      NULL,
    CONSTRAINT PK_Lectoras PRIMARY KEY (IdTerminal, Numero),
    CONSTRAINT CK_Lectoras_Origen CHECK ([ConfigOrigen]='API' OR [ConfigOrigen]='LOCAL'),
    CONSTRAINT CK_Lectoras_Sentido CHECK ([Sentido]='S' OR [Sentido]='E')
);
GO

IF OBJECT_ID(N'dbo.Reles', N'U') IS NULL
CREATE TABLE dbo.Reles (
    Numero               INT                NOT NULL,
    Sentido              CHAR(1)            NOT NULL,
    Comando              VARCHAR(10)        NOT NULL,
    Descripcion          NVARCHAR(150)      NULL,
    FechaModificacion    DATETIME2(0)       NULL,
    ModificadoPor        NVARCHAR(100)      NULL,
    IdTerminal           INT                NOT NULL,
    Activo               BIT                NOT NULL
        CONSTRAINT DF_Reles_Activo DEFAULT ((1)),
    ConfigFecha          DATETIMEOFFSET(0)  NOT NULL
        CONSTRAINT DF_Reles_ConfigFecha DEFAULT (sysdatetimeoffset()),
    ConfigOrigen         VARCHAR(10)        NOT NULL
        CONSTRAINT DF_Reles_ConfigOrigen DEFAULT ('LOCAL'),
    ConfigPor            NVARCHAR(100)      NULL,
    Sincronizado         BIT                NOT NULL
        CONSTRAINT DF_Reles_Sync DEFAULT ((1)),
    UltimoDisparo        DATETIMEOFFSET(0)  NULL,
    UltimoError          NVARCHAR(500)      NULL,
    EstadoFecha          DATETIMEOFFSET(0)  NULL,
    CONSTRAINT PK_Reles PRIMARY KEY (IdTerminal, Numero),
    CONSTRAINT CK_Reles_Origen CHECK ([ConfigOrigen]='API' OR [ConfigOrigen]='LOCAL'),
    CONSTRAINT CK_Reles_Sentido CHECK ([Sentido]='S' OR [Sentido]='E')
);
GO

/* --- Claves foráneas --- */
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_Consultas_Marca')
    ALTER TABLE dbo.ConsultasApiExterna ADD CONSTRAINT FK_Consultas_Marca
        FOREIGN KEY (IdMarca) REFERENCES dbo.Marcas (IdMarca);
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_Envios_Marca')
    ALTER TABLE dbo.EnviosBakelite ADD CONSTRAINT FK_Envios_Marca
        FOREIGN KEY (IdMarca) REFERENCES dbo.Marcas (IdMarca);
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_Errores_Marca')
    ALTER TABLE dbo.Errores ADD CONSTRAINT FK_Errores_Marca
        FOREIGN KEY (IdMarca) REFERENCES dbo.Marcas (IdMarca);
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_Errores_Terminal')
    ALTER TABLE dbo.Errores ADD CONSTRAINT FK_Errores_Terminal
        FOREIGN KEY (IdTerminal) REFERENCES dbo.Terminales (IdTerminal);
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_Incidentes_Terminal')
    ALTER TABLE dbo.IncidentesConexion ADD CONSTRAINT FK_Incidentes_Terminal
        FOREIGN KEY (IdTerminal) REFERENCES dbo.Terminales (IdTerminal);
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_Lectoras_Terminal')
    ALTER TABLE dbo.Lectoras ADD CONSTRAINT FK_Lectoras_Terminal
        FOREIGN KEY (IdTerminal) REFERENCES dbo.Terminales (IdTerminal);
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_Marcas_Terminal')
    ALTER TABLE dbo.Marcas ADD CONSTRAINT FK_Marcas_Terminal
        FOREIGN KEY (IdTerminal) REFERENCES dbo.Terminales (IdTerminal);
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_Marcas_Trabajador')
    ALTER TABLE dbo.Marcas ADD CONSTRAINT FK_Marcas_Trabajador
        FOREIGN KEY (IdTrabajador) REFERENCES dbo.Trabajadores (IdTrabajador);
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_Marcas_Version')
    ALTER TABLE dbo.Marcas ADD CONSTRAINT FK_Marcas_Version
        FOREIGN KEY (IdVersion) REFERENCES dbo.Versiones (IdVersion);
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_Reles_Terminal')
    ALTER TABLE dbo.Reles ADD CONSTRAINT FK_Reles_Terminal
        FOREIGN KEY (IdTerminal) REFERENCES dbo.Terminales (IdTerminal);
GO

/* --- Índices --- */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Consultas_Marca' AND object_id = OBJECT_ID(N'dbo.ConsultasApiExterna'))
    CREATE INDEX IX_Consultas_Marca ON dbo.ConsultasApiExterna (IdMarca);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Errores_Fecha' AND object_id = OBJECT_ID(N'dbo.Errores'))
    CREATE INDEX IX_Errores_Fecha ON dbo.Errores (FechaHora);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Incidentes_Pendientes' AND object_id = OBJECT_ID(N'dbo.IncidentesConexion'))
    CREATE INDEX IX_Incidentes_Pendientes ON dbo.IncidentesConexion (IdIncidente) WHERE ([EstadoEnvio]='PENDIENTE' AND [FechaRecuperacion] IS NOT NULL);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'UX_Incidentes_Abierto' AND object_id = OBJECT_ID(N'dbo.IncidentesConexion'))
    CREATE UNIQUE INDEX UX_Incidentes_Abierto ON dbo.IncidentesConexion (Servicio) WHERE ([FechaRecuperacion] IS NULL);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'UX_Incidentes_Uuid' AND object_id = OBJECT_ID(N'dbo.IncidentesConexion'))
    CREATE UNIQUE INDEX UX_Incidentes_Uuid ON dbo.IncidentesConexion (IdTerminal, IdIncidenteUuid) WHERE ([IdIncidenteUuid] IS NOT NULL);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Lectoras_Pendientes' AND object_id = OBJECT_ID(N'dbo.Lectoras'))
    CREATE INDEX IX_Lectoras_Pendientes ON dbo.Lectoras (IdTerminal) WHERE ([Sincronizado]=(0));
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'UX_Lectoras_Sentido' AND object_id = OBJECT_ID(N'dbo.Lectoras'))
    CREATE UNIQUE INDEX UX_Lectoras_Sentido ON dbo.Lectoras (IdTerminal, Sentido) WHERE ([Activa]=(1));
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Marcas_Pendientes' AND object_id = OBJECT_ID(N'dbo.Marcas'))
    CREATE INDEX IX_Marcas_Pendientes ON dbo.Marcas (IdMarca) WHERE ([EstadoEnvio]='PENDIENTE');
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Marcas_Rut' AND object_id = OBJECT_ID(N'dbo.Marcas'))
    CREATE INDEX IX_Marcas_Rut ON dbo.Marcas (Rut, FechaHora);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Reles_Pendientes' AND object_id = OBJECT_ID(N'dbo.Reles'))
    CREATE INDEX IX_Reles_Pendientes ON dbo.Reles (IdTerminal) WHERE ([Sincronizado]=(0));
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'UX_Reles_Sentido' AND object_id = OBJECT_ID(N'dbo.Reles'))
    CREATE UNIQUE INDEX UX_Reles_Sentido ON dbo.Reles (IdTerminal, Sentido) WHERE ([Activo]=(1));
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'UX_Versiones_UnaActiva' AND object_id = OBJECT_ID(N'dbo.Versiones'))
    CREATE UNIQUE INDEX UX_Versiones_UnaActiva ON dbo.Versiones (Activo) WHERE ([Activo]=(1));
GO


/* ---------------------------------------------------------------------------
   4. Datos minimos
      Sin estas filas la aplicacion no funciona: son la identidad del terminal
      y el mapeo de sus dispositivos.
   --------------------------------------------------------------------------- */

/* El terminal. El IdTerminal debe coincidir con config.ID_TERMINAL y con el
   que exista en Bakelite: es la clave con la que se identifican las marcas. */
IF NOT EXISTS (SELECT 1 FROM dbo.Terminales WHERE IdTerminal = 1)
    INSERT dbo.Terminales (IdTerminal, Nombre) VALUES (1, N'Terminal 1');
GO

/* Version de la aplicacion. Cada marca queda asociada a la version activa. */
IF NOT EXISTS (SELECT 1 FROM dbo.Versiones)
    INSERT dbo.Versiones (Numero, SubidoPor, Notas, Activo)
    VALUES ('1.0.0', N'instalacion', N'Version inicial del torniquete.', 1);
GO

/* Estado de los servicios externos que se vigilan. */
IF NOT EXISTS (SELECT 1 FROM dbo.EstadoServicios WHERE Servicio = 'BAKELITE')
    INSERT dbo.EstadoServicios (Servicio, Descripcion)
    VALUES ('BAKELITE', N'API de Bakelite (marcas y presencia)');
GO
IF NOT EXISTS (SELECT 1 FROM dbo.EstadoServicios WHERE Servicio = 'EXTERNA')
    INSERT dbo.EstadoServicios (Servicio, Descripcion)
    VALUES ('EXTERNA', N'API externa que autoriza el acceso');
GO

/* Lectoras y reles. El mapeo de fabrica es el "cruzado" de la especificacion:
   el rele 1 (R2*) abre la ENTRADA. Si el cableado real esta al reves, se
   corrige desde Ajustes y queda guardado aqui. */
IF NOT EXISTS (SELECT 1 FROM dbo.Lectoras WHERE IdTerminal = 1)
    INSERT dbo.Lectoras (IdTerminal, Numero, Sentido, Descripcion)
    VALUES (1, 1, 'E', N'Lectora 1'), (1, 2, 'S', N'Lectora 2');
GO

IF NOT EXISTS (SELECT 1 FROM dbo.Reles WHERE IdTerminal = 1)
    INSERT dbo.Reles (IdTerminal, Numero, Sentido, Comando, Descripcion)
    VALUES (1, 1, 'E', 'R2*', N'Rele 1'), (1, 2, 'S', 'R1*', N'Rele 2');
GO

/* ---------------------------------------------------------------------------
   5. Verificacion
   --------------------------------------------------------------------------- */
PRINT '';
PRINT '=== RESUMEN ===';
SELECT COUNT(*) AS Tablas FROM sys.tables;
SELECT IdTerminal, Nombre, Activo FROM dbo.Terminales;
SELECT Numero, Sentido, Descripcion FROM dbo.Lectoras ORDER BY Numero;
SELECT Numero, Sentido, Comando FROM dbo.Reles ORDER BY Numero;
SELECT Numero AS Version, Activo FROM dbo.Versiones;
PRINT '';
PRINT '>> Listo. Probar la conexion de la app con:';
PRINT '   python3 -c "import basedatos; print(basedatos.BDLocal().terminal())"';
GO
