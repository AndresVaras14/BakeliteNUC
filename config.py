# -*- coding: utf-8 -*-
"""
Configuración central del sistema de control de acceso.
Basada en el bloque §14 de ESPECIFICACION_HARDWARE.md.
Cambiar aquí y nada más para reconfigurar el equipo.
"""

# ===== IDENTIDAD DEL EQUIPO =====
CENTRO = "CE05"
RELOJ = "T010"
MARCA = "BAKELITE"
APP_TITULO = "CONTROL DE ACCESO"
SUBTITULO = "Solo lee cédula de identidad"
TERMINAL_NOMBRE = "Terminal Principal · Edificio A"

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
ARDUINO_SLEEP_APERTURA = 1.0   # el Uno se auto-resetea al abrir el puerto

# ===== TIEMPOS Y WATCHDOGS =====
VALIDACION_TIMEOUT_SEGUNDOS = 10
MENSAJE_SIN_CONEXION_SEGUNDOS = 10
LECTURA_INCOMPLETA_TIMEOUT_SEGUNDOS = 2.0
LECTORA_WATCHDOG_REABRIR_SEGUNDOS = 3600
LECTORA_MAX_ERRORES_CONSECUTIVOS = 5
HEALTH_CHECK_INTERVALO_SEGUNDOS = 60
SCAN_PUERTOS_INTERVALO_SEGUNDOS = 10

# ===== UI =====
SEGUNDOS_MOSTRAR_RESULTADO = 5     # cuánto se mantiene el resultado antes de volver a "esperando"
APAGAR_LUZ_AMARILLA_DESPUES = 3    # LOFF* diferido tras amarillo (código 4)

# ===== VALIDACIÓN (modo prueba con JSON) =====
ARCHIVO_PERSONAS = "personas.json"
# Simula la latencia de la BD/WebService. La luz AZUL se mantiene (relé + pantalla)
# durante toda la consulta, hasta que llega la respuesta.
VALIDACION_DELAY_SIMULADO = 0.7

# ===== VID:PID relevantes =====
CH340_VIDPID = "1a86:7523"   # CH340 (lectoras Aigather y clones)

# ===== REINICIO PROGRAMADO (dejar en 0 para pruebas) =====
KILL1 = 0
HOR_RB = "01:30"
REINICIA_APLICACION = 0
