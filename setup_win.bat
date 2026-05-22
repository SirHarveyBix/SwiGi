@echo off
REM === SwiGi — Setup Windows ===
REM Lance depuis le dossier ou tous les fichiers sont presents.

echo === SwiGi — Installation Windows ===
echo.

REM Creer le dossier d'installation
mkdir "%USERPROFILE%\SwiGi" 2>nul

REM Copier Python embeddable
if exist "%~dp0python-3\python.exe" (
    xcopy /E /I /Y "%~dp0python-3" "%USERPROFILE%\SwiGi\python"
    echo [OK] Python copie
) else (
    echo [ERREUR] Dossier python-3 avec python.exe introuvable dans %~dp0
    echo         Telecharge Python embeddable depuis python.org/downloads/windows/
    echo         Dezippe dans un sous-dossier python-3\
    pause
    exit /b 1
)

REM Copier hidapi.dll
if exist "%~dp0hidapi.dll" (
    copy /Y "%~dp0hidapi.dll" "%USERPROFILE%\SwiGi\"
    copy /Y "%~dp0hidapi.dll" "%USERPROFILE%\SwiGi\python\"
    echo [OK] hidapi.dll copie
) else if exist "%~dp0x64\hidapi.dll" (
    copy /Y "%~dp0x64\hidapi.dll" "%USERPROFILE%\SwiGi\"
    copy /Y "%~dp0x64\hidapi.dll" "%USERPROFILE%\SwiGi\python\"
    echo [OK] hidapi.dll copie depuis x64\
) else (
    echo [ERREUR] hidapi.dll introuvable dans %~dp0
    echo         Telecharge depuis github.com/libusb/hidapi/releases
    echo         Assets > hidapi-win.zip > x64\hidapi.dll
    pause
    exit /b 1
)

REM Copier swigi.py
if exist "%~dp0swigi.py" (
    copy /Y "%~dp0swigi.py" "%USERPROFILE%\SwiGi\"
    echo [OK] swigi.py copie
) else (
    echo [ERREUR] swigi.py introuvable dans %~dp0
    pause
    exit /b 1
)

REM Creer start.bat (avec fenetre — pour voir les logs)
(
echo @echo off
echo title SwiGi
echo echo === SwiGi ===
echo echo Appuie sur Easy-Switch pour synchroniser clavier et souris.
echo echo Ctrl+C pour quitter.
echo echo.
echo "%%~dp0python\python.exe" "%%~dp0swigi.py"
echo pause
) > "%USERPROFILE%\SwiGi\start.bat"
echo [OK] start.bat cree

REM Creer start_verbose.bat (mode debug)
(
echo @echo off
echo title SwiGi [verbose]
echo "%%~dp0python\python.exe" "%%~dp0swigi.py" -v
echo pause
) > "%USERPROFILE%\SwiGi\start_verbose.bat"
echo [OK] start_verbose.bat cree

REM Demarrage automatique via VBScript dans le dossier Startup
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "VBS=%STARTUP%\SwiGi.vbs"

(
echo ' SwiGi — demarrage automatique silencieux
echo Set WshShell = CreateObject("WScript.Shell"^)
echo WshShell.Run Chr(34^) ^& "%USERPROFILE%\SwiGi\python\pythonw.exe" ^& Chr(34^) ^& " " ^& Chr(34^) ^& "%USERPROFILE%\SwiGi\swigi.py" ^& Chr(34^), 0, False
) > "%VBS%"
echo [OK] Demarrage automatique configure (au login Windows)

echo.
echo === INSTALLATION TERMINEE ===
echo.
echo  Dossier       : %USERPROFILE%\SwiGi\
echo  Demarrage auto: au prochain login Windows, SwiGi se lance seul
echo.
echo  Pour lancer maintenant : ouvre %USERPROFILE%\SwiGi\ et double-clique start.bat
echo  Pour desactiver l'auto : supprime %VBS%
echo.
echo Ouverture du dossier...
explorer "%USERPROFILE%\SwiGi"
pause
