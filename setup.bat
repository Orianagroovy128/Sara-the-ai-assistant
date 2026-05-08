@echo off
echo [DevOps] Initializing S.A.R.A installation for Windows...

:: Check Python
python --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo Python could not be found. Please install Python 3.9+ and add to PATH.
    pause
    exit /b
)

:: Create Virtual Environment
echo [DevOps] Creating virtual environment...
python -m venv sara_env
call sara_env\Scripts\activate.bat

:: Install Dependencies
echo [DevOps] Installing Python dependencies...
python -m pip install --upgrade pip
pip install pipwin
pipwin install pyaudio
pip install -r requirements.txt

:: Check Ollama
ollama --version >nul 2>&1
IF %ERRORLEVEL% NEQ 0 (
    echo [DevOps] WARNING: Ollama is not installed. Please install from https://ollama.com
) ELSE (
    echo [DevOps] Local model check... make sure to run 'ollama run gemma4:e4b' manually first.
)

echo.
echo =======================================================
echo [DevOps] Setup Complete! 
echo To run S.A.R.A, type the following two commands:
echo 1. sara_env\Scripts\activate
echo 2. python sara.py
echo =======================================================
pause
