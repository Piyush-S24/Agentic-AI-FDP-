@echo off
echo ============================================================
echo   EdU Revolution - Starting Server
echo ============================================================
echo.

REM Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found!
    pause
    exit /b 1
)

REM Check .env
if not exist ".env" (
    echo ERROR: .env file not found!
    echo Run setup.bat first, then edit .env with your Groq API key.
    pause
    exit /b 1
)

REM Check static folder
if not exist "static\index.html" (
    echo ERROR: static/index.html not found!
    echo Make sure the static folder exists with all frontend files.
    pause
    exit /b 1
)

REM Ensure directories
if not exist "uploads" mkdir uploads
if not exist "chroma_db" mkdir chroma_db

echo Starting server at: http://localhost:8000
echo.
echo   - Open this URL in your browser
echo   - Upload PDFs using the sidebar
echo   - Press Ctrl+C to stop the server
echo.

python -m uvicorn app:app --reload --host 127.0.0.1 --port 8000
if %errorlevel% neq 0 (
    echo.
    echo Server failed to start. Common fixes:
    echo   1. Run setup.bat first to install dependencies
    echo   2. Make sure port 8000 is not in use
    echo   3. Check the error message above
    pause
)
