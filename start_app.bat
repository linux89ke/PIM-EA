@echo off
cd /d "%~dp0"

:: Find a free port starting from 8501
set PORT=8505
:find_port
netstat -ano | findstr ":%PORT% " >nul 2>&1
if %errorlevel%==0 (
    set /a PORT+=1
    goto find_port
)

start "" http://localhost:%PORT%
:: Added the maxUploadSize argument to the line below!
streamlit run streamlit_app.py --server.port %PORT% --server.maxUploadSize 500
