/* ===========================================================================
   BakeliteTorniquete — Lectoras y reles: sincronizacion y estado
   ---------------------------------------------------------------------------
   Deja dbo.Lectoras y dbo.Reles con la estructura de
   CONTRATO_DISPOSITIVOS_TERMINAL.md:

     - IdTerminal en la clave primaria (un mismo numero existe en cada terminal)
     - campos de configuracion sincronizable (ConfigFecha / Origen / Por)
     - campos de estado observado (conectada, puerto, ultima lectura...)
     - baja logica en vez de DELETE

   Es idempotente: se puede correr varias veces sin efecto.
   Requiere que 01_crear_BakeliteTorniquete.sql ya se haya ejecutado.
   =========================================================================== */

USE BakeliteTorniquete;
GO

/* ---------------------------------------------------------------------------
   1. Columnas nuevas
      Todo se agrega NULL primero y se endurece al final: asi corre sobre las
      tablas que ya tienen datos.
   --------------------------------------------------------------------------- */

/* --- dbo.Lectoras --- */
IF COL_LENGTH(N'dbo.Lectoras', N'IdTerminal') IS NULL
    ALTER TABLE dbo.Lectoras ADD IdTerminal INT NULL;
GO
IF COL_LENGTH(N'dbo.Lectoras', N'Activa') IS NULL
    ALTER TABLE dbo.Lectoras ADD Activa BIT NULL;          -- baja logica
GO
IF COL_LENGTH(N'dbo.Lectoras', N'ConfigFecha') IS NULL
    ALTER TABLE dbo.Lectoras ADD ConfigFecha DATETIMEOFFSET(0) NULL;
GO
IF COL_LENGTH(N'dbo.Lectoras', N'ConfigOrigen') IS NULL
    ALTER TABLE dbo.Lectoras ADD ConfigOrigen VARCHAR(10) NULL;   -- LOCAL / API
GO
IF COL_LENGTH(N'dbo.Lectoras', N'ConfigPor') IS NULL
    ALTER TABLE dbo.Lectoras ADD ConfigPor NVARCHAR(100) NULL;
GO
IF COL_LENGTH(N'dbo.Lectoras', N'Sincronizado') IS NULL
    ALTER TABLE dbo.Lectoras ADD Sincronizado BIT NULL;    -- 0 = falta subirlo
GO
IF COL_LENGTH(N'dbo.Lectoras', N'Conectada') IS NULL
    ALTER TABLE dbo.Lectoras ADD Conectada BIT NULL;       -- NULL = sin dato aun
GO
IF COL_LENGTH(N'dbo.Lectoras', N'UltimaLectura') IS NULL
    ALTER TABLE dbo.Lectoras ADD UltimaLectura DATETIMEOFFSET(0) NULL;
GO
IF COL_LENGTH(N'dbo.Lectoras', N'UltimoError') IS NULL
    ALTER TABLE dbo.Lectoras ADD UltimoError NVARCHAR(500) NULL;
GO
IF COL_LENGTH(N'dbo.Lectoras', N'EstadoFecha') IS NULL
    ALTER TABLE dbo.Lectoras ADD EstadoFecha DATETIMEOFFSET(0) NULL;
GO

/* --- dbo.Reles --- */
IF COL_LENGTH(N'dbo.Reles', N'IdTerminal') IS NULL
    ALTER TABLE dbo.Reles ADD IdTerminal INT NULL;
GO
IF COL_LENGTH(N'dbo.Reles', N'Activo') IS NULL
    ALTER TABLE dbo.Reles ADD Activo BIT NULL;
GO
IF COL_LENGTH(N'dbo.Reles', N'ConfigFecha') IS NULL
    ALTER TABLE dbo.Reles ADD ConfigFecha DATETIMEOFFSET(0) NULL;
GO
IF COL_LENGTH(N'dbo.Reles', N'ConfigOrigen') IS NULL
    ALTER TABLE dbo.Reles ADD ConfigOrigen VARCHAR(10) NULL;
GO
IF COL_LENGTH(N'dbo.Reles', N'ConfigPor') IS NULL
    ALTER TABLE dbo.Reles ADD ConfigPor NVARCHAR(100) NULL;
GO
IF COL_LENGTH(N'dbo.Reles', N'Sincronizado') IS NULL
    ALTER TABLE dbo.Reles ADD Sincronizado BIT NULL;
GO
IF COL_LENGTH(N'dbo.Reles', N'UltimoDisparo') IS NULL
    ALTER TABLE dbo.Reles ADD UltimoDisparo DATETIMEOFFSET(0) NULL;
GO
IF COL_LENGTH(N'dbo.Reles', N'UltimoError') IS NULL
    ALTER TABLE dbo.Reles ADD UltimoError NVARCHAR(500) NULL;
GO
IF COL_LENGTH(N'dbo.Reles', N'EstadoFecha') IS NULL
    ALTER TABLE dbo.Reles ADD EstadoFecha DATETIMEOFFSET(0) NULL;
GO

/* --- Estado del Arduino: hay uno por terminal, va en dbo.Terminales --- */
IF COL_LENGTH(N'dbo.Terminales', N'ArduinoConectado') IS NULL
    ALTER TABLE dbo.Terminales ADD ArduinoConectado BIT NULL;
GO
IF COL_LENGTH(N'dbo.Terminales', N'ArduinoPuerto') IS NULL
    ALTER TABLE dbo.Terminales ADD ArduinoPuerto NVARCHAR(100) NULL;
GO
IF COL_LENGTH(N'dbo.Terminales', N'ArduinoEstadoFecha') IS NULL
    ALTER TABLE dbo.Terminales ADD ArduinoEstadoFecha DATETIMEOFFSET(0) NULL;
GO

/* ---------------------------------------------------------------------------
   2. Relleno de las filas que ya existen
      El terminal de este equipo es el 1 (config.ID_TERMINAL). Las filas
      previas se dan por configuradas localmente y ya sincronizadas: no hay
      nada que subir hasta que alguien cambie algo.
   --------------------------------------------------------------------------- */
UPDATE dbo.Lectoras
   SET IdTerminal   = COALESCE(IdTerminal, 1),
       Activa       = COALESCE(Activa, 1),
       ConfigFecha  = COALESCE(ConfigFecha,
                               TODATETIMEOFFSET(COALESCE(FechaModificacion,
                                                         SYSDATETIME()),
                                                DATEPART(TZOFFSET, SYSDATETIMEOFFSET()))),
       ConfigOrigen = COALESCE(ConfigOrigen, 'LOCAL'),
       Sincronizado = COALESCE(Sincronizado, 1)
 WHERE IdTerminal IS NULL OR Activa IS NULL OR ConfigFecha IS NULL
    OR ConfigOrigen IS NULL OR Sincronizado IS NULL;
GO

UPDATE dbo.Reles
   SET IdTerminal   = COALESCE(IdTerminal, 1),
       Activo       = COALESCE(Activo, 1),
       ConfigFecha  = COALESCE(ConfigFecha,
                               TODATETIMEOFFSET(COALESCE(FechaModificacion,
                                                         SYSDATETIME()),
                                                DATEPART(TZOFFSET, SYSDATETIMEOFFSET()))),
       ConfigOrigen = COALESCE(ConfigOrigen, 'LOCAL'),
       Sincronizado = COALESCE(Sincronizado, 1)
 WHERE IdTerminal IS NULL OR Activo IS NULL OR ConfigFecha IS NULL
    OR ConfigOrigen IS NULL OR Sincronizado IS NULL;
GO

/* ---------------------------------------------------------------------------
   3. Valores por defecto para las filas nuevas
   --------------------------------------------------------------------------- */
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name = N'DF_Lectoras_Activa')
    ALTER TABLE dbo.Lectoras ADD CONSTRAINT DF_Lectoras_Activa DEFAULT (1) FOR Activa;
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name = N'DF_Lectoras_ConfigFecha')
    ALTER TABLE dbo.Lectoras ADD CONSTRAINT DF_Lectoras_ConfigFecha
        DEFAULT (SYSDATETIMEOFFSET()) FOR ConfigFecha;
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name = N'DF_Lectoras_ConfigOrigen')
    ALTER TABLE dbo.Lectoras ADD CONSTRAINT DF_Lectoras_ConfigOrigen
        DEFAULT ('LOCAL') FOR ConfigOrigen;
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name = N'DF_Lectoras_Sync')
    ALTER TABLE dbo.Lectoras ADD CONSTRAINT DF_Lectoras_Sync DEFAULT (1) FOR Sincronizado;
GO

IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name = N'DF_Reles_Activo')
    ALTER TABLE dbo.Reles ADD CONSTRAINT DF_Reles_Activo DEFAULT (1) FOR Activo;
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name = N'DF_Reles_ConfigFecha')
    ALTER TABLE dbo.Reles ADD CONSTRAINT DF_Reles_ConfigFecha
        DEFAULT (SYSDATETIMEOFFSET()) FOR ConfigFecha;
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name = N'DF_Reles_ConfigOrigen')
    ALTER TABLE dbo.Reles ADD CONSTRAINT DF_Reles_ConfigOrigen
        DEFAULT ('LOCAL') FOR ConfigOrigen;
GO
IF NOT EXISTS (SELECT 1 FROM sys.default_constraints WHERE name = N'DF_Reles_Sync')
    ALTER TABLE dbo.Reles ADD CONSTRAINT DF_Reles_Sync DEFAULT (1) FOR Sincronizado;
GO

/* ---------------------------------------------------------------------------
   4. Columnas obligatorias
   --------------------------------------------------------------------------- */
IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'dbo.Lectoras')
             AND name = N'IdTerminal' AND is_nullable = 1)
    ALTER TABLE dbo.Lectoras ALTER COLUMN IdTerminal INT NOT NULL;
GO
IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'dbo.Lectoras')
             AND name = N'Activa' AND is_nullable = 1)
    ALTER TABLE dbo.Lectoras ALTER COLUMN Activa BIT NOT NULL;
GO
IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'dbo.Lectoras')
             AND name = N'ConfigFecha' AND is_nullable = 1)
    ALTER TABLE dbo.Lectoras ALTER COLUMN ConfigFecha DATETIMEOFFSET(0) NOT NULL;
GO
IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'dbo.Lectoras')
             AND name = N'ConfigOrigen' AND is_nullable = 1)
    ALTER TABLE dbo.Lectoras ALTER COLUMN ConfigOrigen VARCHAR(10) NOT NULL;
GO
IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'dbo.Lectoras')
             AND name = N'Sincronizado' AND is_nullable = 1)
    ALTER TABLE dbo.Lectoras ALTER COLUMN Sincronizado BIT NOT NULL;
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'dbo.Reles')
             AND name = N'IdTerminal' AND is_nullable = 1)
    ALTER TABLE dbo.Reles ALTER COLUMN IdTerminal INT NOT NULL;
GO
IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'dbo.Reles')
             AND name = N'Activo' AND is_nullable = 1)
    ALTER TABLE dbo.Reles ALTER COLUMN Activo BIT NOT NULL;
GO
IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'dbo.Reles')
             AND name = N'ConfigFecha' AND is_nullable = 1)
    ALTER TABLE dbo.Reles ALTER COLUMN ConfigFecha DATETIMEOFFSET(0) NOT NULL;
GO
IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'dbo.Reles')
             AND name = N'ConfigOrigen' AND is_nullable = 1)
    ALTER TABLE dbo.Reles ALTER COLUMN ConfigOrigen VARCHAR(10) NOT NULL;
GO
IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'dbo.Reles')
             AND name = N'Sincronizado' AND is_nullable = 1)
    ALTER TABLE dbo.Reles ALTER COLUMN Sincronizado BIT NOT NULL;
GO

/* ---------------------------------------------------------------------------
   5. Clave primaria (IdTerminal, Numero)
      El numero de lectora se repite entre terminales: por si solo no
      identifica nada. Se rehace la PK para que la tabla sirva igual si algun
      dia esta base guarda mas de un terminal.
   --------------------------------------------------------------------------- */
IF EXISTS (SELECT 1 FROM sys.key_constraints
            WHERE name = N'PK_Lectoras' AND type = 'PK'
              AND parent_object_id = OBJECT_ID(N'dbo.Lectoras')
              AND (SELECT COUNT(*) FROM sys.index_columns ic
                    WHERE ic.object_id = OBJECT_ID(N'dbo.Lectoras')
                      AND ic.index_id = unique_index_id) = 1)
BEGIN
    ALTER TABLE dbo.Lectoras DROP CONSTRAINT PK_Lectoras;
    ALTER TABLE dbo.Lectoras ADD CONSTRAINT PK_Lectoras
        PRIMARY KEY (IdTerminal, Numero);
END
GO

IF EXISTS (SELECT 1 FROM sys.key_constraints
            WHERE name = N'PK_Reles' AND type = 'PK'
              AND parent_object_id = OBJECT_ID(N'dbo.Reles')
              AND (SELECT COUNT(*) FROM sys.index_columns ic
                    WHERE ic.object_id = OBJECT_ID(N'dbo.Reles')
                      AND ic.index_id = unique_index_id) = 1)
BEGIN
    ALTER TABLE dbo.Reles DROP CONSTRAINT PK_Reles;
    ALTER TABLE dbo.Reles ADD CONSTRAINT PK_Reles
        PRIMARY KEY (IdTerminal, Numero);
END
GO

/* ---------------------------------------------------------------------------
   6. Integridad
   --------------------------------------------------------------------------- */
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_Lectoras_Terminal')
    ALTER TABLE dbo.Lectoras ADD CONSTRAINT FK_Lectoras_Terminal
        FOREIGN KEY (IdTerminal) REFERENCES dbo.Terminales (IdTerminal);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_Reles_Terminal')
    ALTER TABLE dbo.Reles ADD CONSTRAINT FK_Reles_Terminal
        FOREIGN KEY (IdTerminal) REFERENCES dbo.Terminales (IdTerminal);
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = N'CK_Lectoras_Origen')
    ALTER TABLE dbo.Lectoras ADD CONSTRAINT CK_Lectoras_Origen
        CHECK (ConfigOrigen IN ('LOCAL','API'));
GO
IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = N'CK_Reles_Origen')
    ALTER TABLE dbo.Reles ADD CONSTRAINT CK_Reles_Origen
        CHECK (ConfigOrigen IN ('LOCAL','API'));
GO

/* El indice unico de sentido pasa a ser por terminal, y solo entre los activos:
   una lectora dada de baja puede repetir el sentido de una activa. */
IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'UX_Lectoras_Sentido'
             AND object_id = OBJECT_ID(N'dbo.Lectoras'))
    DROP INDEX UX_Lectoras_Sentido ON dbo.Lectoras;
GO
CREATE UNIQUE INDEX UX_Lectoras_Sentido
    ON dbo.Lectoras (IdTerminal, Sentido) WHERE Activa = 1;
GO

IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'UX_Reles_Sentido'
             AND object_id = OBJECT_ID(N'dbo.Reles'))
    DROP INDEX UX_Reles_Sentido ON dbo.Reles;
GO
CREATE UNIQUE INDEX UX_Reles_Sentido
    ON dbo.Reles (IdTerminal, Sentido) WHERE Activo = 1;
GO

/* Lo pendiente de subir a Bakelite se consulta seguido: conviene indexarlo. */
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Lectoras_Pendientes')
    CREATE INDEX IX_Lectoras_Pendientes ON dbo.Lectoras (IdTerminal)
        WHERE Sincronizado = 0;
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'IX_Reles_Pendientes')
    CREATE INDEX IX_Reles_Pendientes ON dbo.Reles (IdTerminal)
        WHERE Sincronizado = 0;
GO

/* ---------------------------------------------------------------------------
   7. Verificacion
   --------------------------------------------------------------------------- */
SELECT Numero, IdTerminal, Sentido, Activa, ConfigOrigen, ConfigFecha,
       Sincronizado, Conectada, UltimoPuerto, UltimaLectura
  FROM dbo.Lectoras ORDER BY Numero;

SELECT Numero, IdTerminal, Sentido, Comando, Activo, ConfigOrigen, ConfigFecha,
       Sincronizado, UltimoDisparo
  FROM dbo.Reles ORDER BY Numero;
GO

/* ---------------------------------------------------------------------------
   8. Ancla fisica de cada lectora
      Las dos lectoras son CH340 identicas y NO tienen numero de serie: udev
      devuelve el mismo ID_SERIAL para ambas. Lo unico que las distingue es el
      zocalo USB donde estan enchufadas (ID_PATH), que es estable.

      Sin esto, la deteccion las asignaba por orden de aparicion: al desenchufar
      una, la sobreviviente ocupaba el lugar de la otra y el sistema informaba
      desconectada a la que no era.
   --------------------------------------------------------------------------- */
IF COL_LENGTH(N'dbo.Lectoras', N'Ancla') IS NULL
    ALTER TABLE dbo.Lectoras ADD Ancla NVARCHAR(200) NULL;
GO
