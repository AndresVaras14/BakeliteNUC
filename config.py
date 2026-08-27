# -*- coding: utf-8 -*-
"""
Configuración central del sistema de control de acceso.
Basada en el bloque §14 de ESPECIFICACION_HARDWARE.md.
Cambiar aquí y nada más para reconfigurar el equipo.
"""

import os

# Carpeta del proyecto: todas las rutas se resuelven aquí, así la app funciona
# sin importar desde dónde se lance (terminal, VS Code, supervisor...).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def ruta(nombre):
    return os.path.join(BASE_DIR, nombre)


# ===== IDENTIDAD DEL EQUIPO =====
CENTRO = "CE05"
RELOJ = "T010"
MARCA = "BAKELITE"
APP_TITULO = "CONTROL DE ACCESO"
SUBTITULO = "Solo lee cédula de identidad"
# Ubicación por defecto si el operador aún no la configuró (se pide en Ajustes).
UBICACION_DEFECTO = ""

# ===== CAPACIDADES DEL HARDWARE =====
POSEE_RELE = 1        # 1 = hay Arduino con relés
POSEE_PANTALLA = 1    # 1 = hay monitor
POSEE_LUCES = 1       # 1 = hay semáforo/luces vía Arduino

# ===== SENTIDO DE CADA LECTORA =====
SENTIDO_LECTORA1 = "E"   # Entrada
SENTIDO_LECTORA2 = "S"   # Salida

# ===== COMANDOS DE RELÉ (ASCII, terminados en *) =====
# Mapeo "cruzado" a propósito (ver §6.1 de la especificación).
RELE1 = "R2*"   # relé de ENTRADA (se dispara con Lectora 1)
RELE2 = "R1*"   # relé de SALIDA  (se dispara con Lectora 2)

# ===== COMANDOS DE LUZ (semáforo) =====
LUZ_AZUL = "L1B*"      # leyendo / consultando
LUZ_VERDE = "L1G*"     # habilitado
LUZ_ROJA = "L1R*"      # no habilitado / error
LUZ_AMARILLA = "L1Y*"  # sin conexión a red
LUZ_OFF = "LOFF*"      # apagar
BLINK_LISTO = "TL3000*"  # parpadeo "sistema listo"

# ===== PARÁMETROS SERIE =====
BAUD_ARDUINO = 9600
BAUD_LECTORA = 9600
TIMEOUT_ARDUINO = 1      # 1 s (lectura/escritura bloqueante corta)
TIMEOUT_LECTORA = 0      # no bloqueante (poll con in_waiting)
# Cada cuánto se mira si la lectora mandó algo. Era 0,1 s fijo dentro del
# código: esa espera se sumaba entera antes de notar el primer byte. Con 20 ms
# la respuesta se siente inmediata y el consumo de CPU sigue siendo despreciable
# (son dos puertos serie, no un bucle ocupado).
LECTURA_POLL_SEGUNDOS = 0.02
ARDUINO_SLEEP_APERTURA = 1.0   # el Uno se auto-resetea al abrir el puerto

# ===== TIEMPOS Y WATCHDOGS =====
# Cuánto se espera la respuesta de la API externa antes de rendirse. La luz
# azul y el mensaje "consultando" duran exactamente lo que tarde la respuesta;
# pasado este tope se le dice a la persona que vuelva a intentar, en vez de
# dejarla mirando una pantalla que no avanza.
VALIDACION_TIMEOUT_SEGUNDOS = 7
MENSAJE_SIN_CONEXION_SEGUNDOS = 10
LECTURA_INCOMPLETA_TIMEOUT_SEGUNDOS = 2.0
LECTORA_WATCHDOG_REABRIR_SEGUNDOS = 3600
LECTORA_MAX_ERRORES_CONSECUTIVOS = 5
HEALTH_CHECK_INTERVALO_SEGUNDOS = 60
SCAN_PUERTOS_INTERVALO_SEGUNDOS = 10

# ===== UI =====
SEGUNDOS_MOSTRAR_RESULTADO = 5     # cuánto se mantiene el resultado antes de volver a "esperando"
APAGAR_LUZ_AMARILLA_DESPUES = 3    # LOFF* diferido tras amarillo (código 4)

# Anti-doble-lectura: tras procesar una lectura, se ignoran nuevas lecturas de
# esa misma lectora durante este tiempo (evita mandar 2 consultas al servidor).
LECTURA_COOLDOWN_SEGUNDOS = 2.0

# Anti-repetición. Una cédula apoyada sobre el lector se lee una y otra vez sin
# parar; una cédula que la persona vuelve a pasar llega DESPUÉS de un silencio.
# Esa pausa es la que distingue los dos casos: si entre lectura y lectura de la
# misma cédula pasó menos que esto, sigue apoyada y se ignora; si pasó más, es
# una pasada nueva y se consulta.
#
# Antes era una ventana de 10 s que se reiniciaba con cada lectura ignorada:
# mientras la cédula estuviera ahí no expiraba nunca, y al sacarla había que
# esperar 10 s. El lector pitaba pero la app no hacía nada.
LECTURA_PAUSA_REINICIO_SEGUNDOS = 1.5

# El relé debe accionarse ANTES de encender la luz verde: la persona ve el
# verde cuando el torniquete ya está liberado. Esta pausa garantiza que el
# Arduino reciba los dos comandos en ese orden y no los procese juntos.
RETARDO_RELE_LUZ_SEGUNDOS = 0.15

# ===== VALIDACIÓN (modo prueba con JSON) =====
ARCHIVO_PERSONAS = ruta("personas.json")
# Latencia artificial de la consulta. Se agregó para imitar lo que tardaría la
# BD/WebService real y poder ver la luz azul en las pruebas. En el equipo
# instalado solo suma espera: 0,7 s en CADA lectura, más de lo que tarda la API
# de verdad. Se deja en 0; súbelo solo si quieres volver a simular demora.
VALIDACION_DELAY_SIMULADO = 0.0

# ===== LOGOS =====
# Carpeta con los logos de marca. El de Bakelite viene en azul oscuro (pensado
# para fondos blancos), así que la interfaz lo recolorea para el fondo oscuro.
DIR_LOGOS = ruta("Logos")
LOGO_BAKELITE = os.path.join(DIR_LOGOS, "logo_c5f64981b64c77713caba6eefa309e69_2x.webp")
LOGO_SOPYTEC = os.path.join(DIR_LOGOS, "logo-sopytec.png")
LOGO_BAKELITE_ALTO = 46      # píxeles de alto en la barra superior
LOGO_SOPYTEC_ALTO = 26       # píxeles de alto en el pie

# ===== ARCHIVOS DE ESTADO / REGISTROS =====
ARCHIVO_AJUSTES = ruta("ajustes.json")
ARCHIVO_REGISTROS = ruta("registros.json")      # JSON con todo lo que se hace (con flags)
DIR_LOGS = ruta("logs")
ARCHIVO_LOG = os.path.join(DIR_LOGS, "app.log")
ARCHIVO_LOG_ERRORES = os.path.join(DIR_LOGS, "errores.log")

# ===== MODO DEBUGGER =====
# Registro paso a paso de lo que se hace y lo que se recibe. Se conserva entre
# reinicios: al entrar en modo debugger se carga lo que ya había, que es lo que
# permite revisar un problema que ocurrió antes.
ARCHIVO_LOG_DEBUG = os.path.join(DIR_LOGS, "debugger.log")
DEBUG_MAX_BYTES = 2_000_000          # al pasarse, se conserva una copia .1
DEBUG_LINEAS_HISTORIAL = 800         # cuántas se muestran al abrir el panel

# ===== SINCRONIZACIÓN (BD local + API) =====
# Cada marca (acceso autorizado/rechazado) se guarda en el JSON (cola local) con
# subido_local=0 y subido_api=0, y el sincronizador la sube a la BD local
# (SQL Server) y a la API de Bakelite (contrato). Lo que quede en 0 se reintenta con espera
# incremental. Ver CONTRATO_INTEGRACION_TORNIQUETE.md.
SINCRONIZAR_INTERVALO_SEGUNDOS = 10
SINCRONIZAR_ESPERA_MAX_SEGUNDOS = 60   # tope de la espera incremental ante fallos de red

# Cada cuánto se comprueba que las APIs siguen respondiendo, aunque no haya
# nada que subir. Es independiente de la espera incremental de arriba: aunque
# los reintentos se separen hasta 60 s, el estado se sigue revisando a este
# ritmo, así el indicador de la pantalla no se queda pegado.
# Todo lo periódico contra Bakelite va al mismo ritmo de 10 s: un solo número
# que recordar, y ningún indicador más viejo que otro.
PING_INTERVALO_SEGUNDOS = 10

USAR_BD_LOCAL = True        # True = escribe en SQL Server (BakeliteTorniquete)
SIMULAR_API = False         # False = envía de verdad a la API (contrato)

# ===== BD LOCAL: SQL SERVER (BakeliteTorniquete) =====
# El esquema lo crea bd/01_crear_BakeliteTorniquete.sql y el dueño de la base
# lo deja bd/00_crear_usuario.sql. Requiere pyodbc + driver ODBC de Microsoft.
SQL_SERVIDOR = os.environ.get("BAKELITE_SQL_SERVIDOR", "localhost")
SQL_BASE = os.environ.get("BAKELITE_SQL_BASE", "BakeliteTorniquete")
SQL_USUARIO = os.environ.get("BAKELITE_SQL_USUARIO", "userBakelite")
SQL_CLAVE = os.environ.get("BAKELITE_SQL_CLAVE", "bakelite123")
SQL_PUERTO = int(os.environ.get("BAKELITE_SQL_PUERTO", "1433"))
# Driver ODBC instalado. Los dos que sirven:
#   "ODBC Driver 18 for SQL Server"  -> driver oficial de Microsoft
#   "FreeTDS"                        -> paquete tdsodbc de Ubuntu
# Con AUTO se usa el de Microsoft si está, y si no FreeTDS (ver basedatos.py).
SQL_DRIVER = os.environ.get("BAKELITE_SQL_DRIVER", "AUTO")
SQL_TRUSTED = False          # True = autenticación de Windows (ignora usuario/clave)
SQL_ENCRYPT = True           # el Driver 18 cifra por defecto
SQL_TRUST_CERT = True        # instancia local con certificado autofirmado
SQL_TIMEOUT_CONEXION = 5     # segundos para abrir la conexión
SQL_TIMEOUT_CONSULTA = 15    # segundos por consulta

# API oficial de Bakelite (contrato de integración).
API_BASE = "https://bakeliteapi.sopytec.cl/"
API_URL = "https://bakeliteapi.sopytec.cl/api/terminal/events"
API_TIMEOUT_SEGUNDOS = 10
# Aviso de cortes de conexión: "hubo un corte desde las XX, recuperado a las XX".
# Mientras esté vacío, los cortes se guardan en dbo.IncidentesConexion con
# EstadoEnvio = 'PENDIENTE' y se informan solos en cuanto se defina la URL.
# Estado de la API en tiempo real (health check). Contrato en
# CONTRATO_ENDPOINTS_PENDIENTES.md: 200 con baseDatos "OK" = hay conexión.
API_URL_PING = "https://bakeliteapi.sopytec.cl/api/terminal/health"
API_URL_INCIDENTES = "https://bakeliteapi.sopytec.cl/api/terminal/incidents"

# Datos del terminal en Bakelite. Se consulta para verificar que el idTerminal
# existe y está activo. El NOMBRE ya no se gobierna desde aquí: se sincroniza
# en ambos sentidos (ver más abajo).
API_URL_TERMINAL = "https://bakeliteapi.sopytec.cl/api/terminal"
ID_TERMINAL = 1             # idTerminal asignado a este torniquete (contrato)

# Tope de seguridad: cuánto puede estar una lectora "ocupada" antes de que se
# la libere por la fuerza. El trámite normal ya tiene sus propios topes (7 s de
# consulta + los de la BD), así que llegar aquí significa que algo quedó colgado
# donde no debía. Sin esta red, esa lectora no volvería a validar nunca y habría
# que reiniciar el equipo.
OCUPADA_MAX_SEGUNDOS = 30

# ===== PRESENCIA (HEARTBEAT) =====
# Le avisa a Bakelite que ESTE PROCESO está vivo. No dice nada del hardware:
# que el heartbeat llegue no significa que las lectoras o el Arduino funcionen.
# La API sella con SU reloj y decide el estado; aquí solo se avisa.
# Contrato: CONTRATO_DISPONIBILIDAD_SOFTWARE_ESCRITORIO.md
API_URL_HEARTBEAT = "https://bakeliteapi.sopytec.cl/api/terminal/{id}/heartbeat"
# Valores por defecto. La API los devuelve en cada respuesta y mandan los suyos:
# estos solo se usan hasta el primer heartbeat aceptado.
HEARTBEAT_INTERVALO_SEGUNDOS = 10
HEARTBEAT_TIMEOUT_SEGUNDOS = 5      # el contrato recomienda 5 s
# Tras un 404 (idTerminal inexistente) no se reintenta al mismo ritmo: sería
# golpear la API cada 10 s por un error de configuración que nadie va a
# corregir solo.
HEARTBEAT_ESPERA_ERROR_SEGUNDOS = 60

# ===== LECTORAS Y RELÉS =====
# El terminal manda la foto completa de sus dispositivos —cómo están
# configurados y en qué estado están— y recibe de vuelta lo que en Bakelite sea
# más reciente. Contrato: CONTRATO_DISPOSITIVOS_TERMINAL.md
API_URL_DISPOSITIVOS = "https://bakeliteapi.sopytec.cl/api/terminal/{id}/dispositivos/sincronizar"
# Valor por defecto: la API manda el suyo en sincronizarCadaSegundos y ese gana.
DISPOSITIVOS_INTERVALO_SEGUNDOS = 10
DISPOSITIVOS_TIMEOUT_SEGUNDOS = 10
# Tras un error de configuración (404/400) no se reintenta al mismo ritmo: sería
# insistir cada 10 s con algo que nadie va a corregir solo.
DISPOSITIVOS_ESPERA_ERROR_SEGUNDOS = 60

# ===== SINCRONIZACIÓN DEL NOMBRE DEL TERMINAL =====
# El nombre se puede cambiar en el NUC (Ajustes) o en la web de Bakelite. Gana
# el cambio más reciente: cada lado guarda la hora exacta en que lo cambió
# (NombreFecha) y esa hora es el único criterio de desempate.
# Contrato: CONTRATO_SINCRONIZACION_NOMBRE_TERMINAL.md
# {id} se reemplaza por ID_TERMINAL.
API_URL_NOMBRE_COMPARAR = "https://bakeliteapi.sopytec.cl/api/terminal/{id}/nombre-terminal/comparar"
API_URL_NOMBRE_HACIA_NUC = "https://bakeliteapi.sopytec.cl/api/terminal/{id}/nombre-terminal/hacia-nuc"
API_URL_NOMBRE_DESDE_NUC = "https://bakeliteapi.sopytec.cl/api/terminal/{id}/nombre-terminal/desde-nuc"
# Cada cuánto se compara el nombre con Bakelite. Va más espaciado que el resto
# a propósito: la comparación no escribe nada y el nombre cambia cada varios
# meses, así que sondearlo cada 10 s serían 8.640 llamadas diarias para nada.
# Un cambio hecho en esta app NO espera este ciclo: se sube al instante. El
# intervalo solo gobierna la otra dirección, enterarse de un cambio hecho en la
# web, que por eso puede tardar hasta un minuto en verse en el terminal.
NOMBRE_SYNC_INTERVALO_SEGUNDOS = 60
# Margen que la API tolera hacia el futuro. Si el reloj del NUC se adelanta más
# que esto, la API responde 400 y el cambio no se sube.
NOMBRE_SYNC_MARGEN_FUTURO_SEGUNDOS = 300

# ===== VID:PID relevantes =====
CH340_VIDPID = "1a86:7523"   # CH340 (lectoras Aigather y clones)

# ===== REINICIO PROGRAMADO (dejar en 0 para pruebas) =====
KILL1 = 0
HOR_RB = "01:30"
REINICIA_APLICACION = 0
