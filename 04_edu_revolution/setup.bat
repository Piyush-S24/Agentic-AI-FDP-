@echo off
echo ============================================================
echo   EdU Revolution - One-Click Setup
echo ============================================================
echo.

REM Check Python is available
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found. Please install Python 3.10+ from python.org
    pause
    exit /b 1
)

echo [1/4] Creating .env file...
if not exist ".env" (
    copy .env.example .env >nul
    echo       Created .env
    echo       *** IMPORTANT: Open .env and replace your_groq_api_key_here ***
    echo       *** with your actual Groq API key from console.groq.com     ***
) else (
    echo       .env already exists - skipping
)

echo.
echo [2/4] Creating directories...
if not exist "uploads" mkdir uploads
if not exist "chroma_db" mkdir chroma_db
if not exist "static" (
    echo       ERROR: static folder missing! Re-download the project.
    pause
    exit /b 1
)
echo       Directories ready

echo.
echo [3/4] Installing Python dependencies...
echo       This may take 5-10 minutes on first run...
echo.
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo.
    echo       *** pip install failed. Trying with --user flag... ***
    pip install --user -r requirements.txt
    if %errorlevel% neq 0 (
        echo.
        echo       ERROR: Could not install dependencies.
        echo       Try running: pip install -r requirements.txt
        echo       manually in a terminal with admin privileges.
        pause
        exit /b 1
    )
)

echo.
echo [4/4] Verifying installation...
python -c "import fastapi; import groq; import chromadb; import sentence_transformers; print('All dependencies OK!')"
if %errorlevel% neq 0 (
    echo       WARNING: Some dependencies may not have installed correctly.
    echo       Try running the server anyway - it will show specific errors.
)

echo.
echo ============================================================
echo   Setup Complete!
echo ============================================================
echo.
echo   NEXT STEPS:
echo   1. Edit .env and set: GROQ_API_KEY=gsk_your_actual_key
echo      Get a free key at: https://console.groq.com
echo.
echo   2. Run: start.bat
echo      Or:  python -m uvicorn app:app --reload
echo.
echo   3. Open: http://localhost:8000
echo.
echo   4. Upload your PDF manuals and start chatting!
echo.
pause
