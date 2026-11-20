@echo off
REM Double-click to open File Organizer (Windows). Needs Python 3.10+.
cd /d "%~dp0"
set "PY=python"
where python >nul 2>nul || set "PY=py -3"
%PY% -c "import PIL, pystray, pypdf" >nul 2>nul
if errorlevel 1 (
  echo Installing the components File Organizer needs ^(first run only^)...
  %PY% -m pip install -r requirements.txt
)
where pythonw >nul 2>nul && (start "" pythonw -m organizer ui) || (start "" pyw -m organizer ui)
