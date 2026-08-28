@echo off
REM ============================================================================
REM  Instalador del control de acceso Bakelite - Windows
REM
REM    instalar_windows.bat              instala todo
REM    instalar_windows.bat /verificar   solo comprueba, no toca nada
REM
REM  Es idempotente: se puede correr las veces que haga falta.
REM ============================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "VERIFICAR=0"
if /i "%~1"=="/verificar" set "VERIFICAR=1"
set /a FALLOS=0
set /a AVISOS=0

echo.
echo  Instalador del control de acceso Bakelite
echo  Carpeta: %CD%
if "%VERIFICAR%"=="1" echo  MODO VERIFICACION: no se modifica nada.

REM ---------------------------------------------------------------------------
echo.
echo === 1. Python ===
set "PYTHON_EXE=%BAKELITE_PYTHON%"
if not defined PYTHON_EXE (
    for /f "delims=" %%p in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%p"
)
if not defined PYTHON_EXE (
    for /f "delims=" %%p in ('python -c "import sys;print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%p"
)
if not defined PYTHON_EXE (
    echo   [X] Python no esta instalado o no esta en el PATH.
    echo       Descargalo de python.org y marca "Add python.exe to PATH".
    set /a FALLOS+=1
    goto :resumen
)
"%PYTHON_EXE%" --version >nul 2>&1
if errorlevel 1 (
    echo   [X] El interprete Python detectado no se pudo ejecutar.
    set /a FALLOS+=1
    goto :resumen
)
for /f "tokens=2" %%v in ('"%PYTHON_EXE%" --version 2^>^&1') do echo   [OK] Python %%v
echo   [OK] Interprete: %PYTHON_EXE%

"%PYTHON_EXE%" -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo   [X] Falta tkinter: la app no puede mostrar la pantalla.
    echo       Reinstala Python marcando la opcion "tcl/tk and IDLE".
    set /a FALLOS+=1
) else (
    echo   [OK] tkinter
)

REM ---------------------------------------------------------------------------
echo.
echo === 2. Paquetes de Python ===
if "%VERIFICAR%"=="1" (
    echo   [.] Modo verificacion: no se instala nada
) else (
    "%PYTHON_EXE%" -m pip install --quiet --upgrade pip >nul 2>&1
    "%PYTHON_EXE%" -m pip install --quiet -r requirements.txt
    if errorlevel 1 (
        echo   [X] Fallo la instalacion de requirements.txt
        set /a FALLOS+=1
    )
)
for %%m in (serial sqlite3 pyodbc pymssql PIL) do (
    "%PYTHON_EXE%" -c "import %%m" >nul 2>&1
    if errorlevel 1 (
        if "%%m"=="PIL" (
            echo   [!] Pillow no esta: la pantalla usa un respaldo mas simple.
            set /a AVISOS+=1
        ) else (
            echo   [X] Falta el modulo %%m
            set /a FALLOS+=1
        )
    ) else (
        echo   [OK] modulo %%m
    )
)

REM ---------------------------------------------------------------------------
echo.
echo === 3. Driver ODBC ===
"%PYTHON_EXE%" -c "import pyodbc;d=pyodbc.drivers();print('   [OK] '+', '.join(d) if d else '   [X] sin drivers')" 2>nul
"%PYTHON_EXE%" -c "import pyodbc,sys;sys.exit(0 if any('SQL Server' in x for x in pyodbc.drivers()) else 1)" >nul 2>&1
if errorlevel 1 (
    "%PYTHON_EXE%" -c "import pymssql" >nul 2>&1
    if errorlevel 1 (
        echo   [X] No hay driver ODBC ni respaldo pymssql.
        echo       Descarga ODBC 18 desde: https://aka.ms/downloadmsodbcsql
        set /a FALLOS+=1
    ) else (
        echo   [!] No hay driver ODBC; se usara automaticamente pymssql.
        set /a AVISOS+=1
    )
)

REM ---------------------------------------------------------------------------
echo.
echo === 4. Hardware conectado ===
"%PYTHON_EXE%" -c "import serial.tools.list_ports as lp; ps=list(lp.comports()); print('\n'.join('   [OK] %s  %s' %% (p.device,p.description) for p in ps)) if ps else print('   [!] sin puertos COM')" 2>nul
"%PYTHON_EXE%" -c "import sys;sys.path.insert(0,'.');import deteccion_puertos as d;r=d.detectar({});[print('   %s %s: %s' %% ('[OK]' if r.get(k) else '[!] ',k,r.get(k) or 'no detectado')) for k in ('arduino','lectora1','lectora2')]" 2>nul

REM ---------------------------------------------------------------------------
echo.
echo === 5. Base de datos ===
"%PYTHON_EXE%" -c "import sys;sys.path.insert(0,'.');import basedatos;t=basedatos.BDLocal().terminal();sys.exit(0 if t else 1)" >nul 2>&1
if errorlevel 1 (
    echo   [!] La base no responde todavia.
    set /a AVISOS+=1
    if "%VERIFICAR%"=="0" (
        echo.
        set /p "CLAVESA=      Clave de 'sa' para crearla ahora (Enter para omitir): "
        if not "!CLAVESA!"=="" (
            where sqlcmd >nul 2>&1
            if errorlevel 1 (
                echo   [X] sqlcmd no esta en el PATH. Ejecuta el script desde SSMS:
                echo       bd\crear_bd_completa.sql
                set /a FALLOS+=1
            ) else (
                sqlcmd -S localhost -U sa -P "!CLAVESA!" -C -i bd\crear_bd_completa.sql
                if errorlevel 1 (
                    echo   [X] Fallo la creacion. Revisa la clave y que TCP/IP este habilitado.
                    set /a FALLOS+=1
                ) else (
                    echo   [OK] Base creada
                )
            )
        ) else (
            echo       Omitido. La app arranca igual: las marcas se encolan en bakelite_nuc.db
        )
    )
) else (
    echo   [OK] La base responde y el terminal esta configurado
)

REM ---------------------------------------------------------------------------
echo.
echo === 6. Arranque automatico y reinicio ante caidas ===
REM supervisor.py relanza la app si se cae; el acceso directo en Inicio la
REM levanta al iniciar sesion. Los dos juntos hacen que el equipo se recupere.
set "INICIO=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "ACCESO=%INICIO%\Bakelite.lnk"
if "%VERIFICAR%"=="1" (
    if exist "%ACCESO%" (echo   [OK] Ya configurado) else (echo   [!] No configurado & set /a AVISOS+=1)
) else (
    REM pythonw evita que quede una consola negra encima de la pantalla.
    set "PYTHONW_EXE=%PYTHON_EXE:python.exe=pythonw.exe%"
    powershell -NoProfile -Command ^
      "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%ACCESO%');" ^
      "$s.TargetPath='%PYTHONW_EXE%';" ^
      "$s.Arguments='\"%CD%\supervisor.py\"';" ^
      "$s.WorkingDirectory='%CD%';" ^
      "$s.Description='Bakelite Control de Acceso';" ^
      "$s.Save()" >nul 2>&1
    if exist "%ACCESO%" (
        echo   [OK] Arranque automatico: %ACCESO%
        echo   [OK] Reinicio ante caidas: lo hace supervisor.py
    ) else (
        echo   [X] No se pudo crear el acceso directo en Inicio.
        set /a FALLOS+=1
    )
)

REM ---------------------------------------------------------------------------
:resumen
echo.
echo === Resumen ===
if %FALLOS%==0 (
    echo   Sin fallos, %AVISOS% aviso^(s^)
    echo.
    echo   Para arrancar ahora:
    echo      "%PYTHON_EXE%" supervisor.py
    if "%VERIFICAR%"=="0" echo   Al iniciar sesion arrancara solo.
) else (
    echo   %FALLOS% fallo^(s^) y %AVISOS% aviso^(s^). Revisa lo marcado arriba.
)
echo.
if not defined BAKELITE_NO_PAUSE pause
endlocal
exit /b %FALLOS%
