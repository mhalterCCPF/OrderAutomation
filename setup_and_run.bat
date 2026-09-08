@echo off
SETLOCAL EnableDelayedExpansion
cd /d "%~dp0"

echo ===================================================
echo     Setting up Order Automation Prerequisites
echo ===================================================
echo.

:: 1. Check for Python Installation
python --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] Python is not installed or not in PATH.
    echo [*] Downloading Python 3.12 Installer...
    powershell -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.2/python-3.12.2-amd64.exe' -OutFile 'python_setup.exe'"
    
    echo [*] Installing Python silently (adding to PATH)...
    python_setup.exe /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
    del python_setup.exe
    
    :: Refresh Environment Variables for Current Batch Session
    set "PATH=%ProgramFiles%\Python312;%ProgramFiles%\Python312\Scripts;%PATH%"
) else (
    echo [+] Python detected.
)

:: 2. Check and Install MSYS2 / GTK3 Runtime for WeasyPrint
if not exist "C:\msys64\mingw64\bin\libgtk-3-0.dll" (
    echo [*] MSYS2/GTK3 runtime not found.
    echo [*] Downloading MSYS2 Installer...
    powershell -Command "Invoke-WebRequest -Uri 'https://github.com/msys2/msys2-installer/releases/download/2024-01-13/msys2-x86_64-20240113.exe' -OutFile 'msys2_setup.exe'"
    
    echo [*] Installing MSYS2 silently...
    msys2_setup.exe --confirm-command --accept-messages --silent
    del msys2_setup.exe
    
    echo [*] Installing GTK3 and Cairo packages via MSYS2 pacman...
    C:\msys64\usr\bin\bash.exe -lc "pacman -S --noconfirm mingw-w64-x86_64-gtk3 mingw-w64-x86_64-pango mingw-w64-x86_64-gobject-introspection"
) else (
    echo [+] GTK3 runtime detected.
)

:: Ensure GTK bin path is present in PATH for current session
set "PATH=C:\msys64\mingw64\bin;%PATH%"

:: 3. Create Virtual Environment if missing
if not exist ".venv" (
    echo [*] Creating Python virtual environment...
    python -m venv .venv
)

:: 4. Activate Virtual Environment & Install Pip Packages
echo [*] Installing Python dependencies from requirements.txt...
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
pip install -r requirements.txt

echo.
echo ===================================================
echo     Setup Complete! Launching Order Automation...
echo ===================================================
echo.

:: 5. Launch the Application
start pythonw app.py