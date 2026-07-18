@echo off
echo ============================================================
echo   EdU Revolution - Verification Script
echo ============================================================
echo.

echo [1/4] Checking Python syntax...
python -c "import ast; files=['config.py','pdf_processor.py','rag_engine.py','app.py']; [ast.parse(open(f).read()) for f in files]; print('  All Python files: SYNTAX OK')"
if %errorlevel% neq 0 (
    echo  FAILED: Syntax errors found
    pause
    exit /b 1
)

echo.
echo [2/4] Checking imports...
python -c "import fastapi; print(f'  FastAPI v{fastapi.__version__}')"
if %errorlevel% neq 0 (
    echo  FAILED: fastapi not installed. Run setup.bat first.
    pause
    exit /b 1
)

python -c "import groq; print('  Groq SDK OK')"
if %errorlevel% neq 0 (
    echo  FAILED: groq not installed. Run setup.bat first.
    pause
    exit /b 1
)

python -c "import chromadb; print(f'  ChromaDB v{chromadb.__version__}')"
if %errorlevel% neq 0 (
    echo  FAILED: chromadb not installed. Run setup.bat first.
    pause
    exit /b 1
)

python -c "import sentence_transformers; print(f'  sentence-transformers v{sentence_transformers.__version__}')"
if %errorlevel% neq 0 (
    echo  FAILED: sentence-transformers not installed. Run setup.bat first.
    pause
    exit /b 1
)

python -c "import PyPDF2; print(f'  PyPDF2 OK')"
if %errorlevel% neq 0 (
    echo  FAILED: PyPDF2 not installed. Run setup.bat first.
    pause
    exit /b 1
)

echo.
echo [3/4] Checking .env file...
if exist ".env" (
    echo   .env file found
    python -c "from dotenv import load_dotenv; import os; load_dotenv(); key=os.getenv('GROQ_API_KEY',''); print(f'  API Key: {\"SET\" if key and key!=\"your_groq_api_key_here\" else \"NOT SET - please edit .env\"}')"
) else (
    echo   WARNING: .env file not found. Run setup.bat first.
)

echo.
echo [4/4] Checking static files...
if exist "static\index.html" (echo   index.html OK) else (echo   MISSING: static\index.html)
if exist "static\styles.css" (echo   styles.css OK) else (echo   MISSING: static\styles.css)
if exist "static\app.js" (echo   app.js OK) else (echo   MISSING: static\app.js)

echo.
echo ============================================================
echo   Verification Complete!
echo ============================================================
echo.
echo   If all checks passed, run: start.bat
echo.
pause
