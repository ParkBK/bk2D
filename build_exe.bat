@echo off
chcp 65001 >nul
rem bk2D.exe 빌드: dist\bk2D.exe 하나로 나온다. (ffmpeg 는 따로 설치되어 PATH 에 있어야 함)
cd /d "%~dp0"
python -m pip install -q pyinstaller || goto :fail
python -m PyInstaller --noconfirm --onefile --windowed --name bk2D --distpath dist --workpath build --specpath build bk2D.pyw || goto :fail
echo.
echo 완료: %~dp0dist\bk2D.exe
pause
exit /b 0
:fail
echo 빌드 실패
pause
exit /b 1
