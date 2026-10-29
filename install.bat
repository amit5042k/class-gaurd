@echo off
cd /d "%~dp0"
echo === ClassGuard installation ===
python -m venv .venv || goto :error
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || goto :error

set /p ANS="Install face recognition (needed for attendance)? [Y/n] "
if /I not "%ANS%"=="n" (
    python -m pip install "setuptools<81" dlib-bin face_recognition_models Click Pillow || goto :error
    python -m pip install --no-deps face_recognition || goto :error
)
set /p ANS="Install emotion analysis (DeepFace, about 1 GB)? [y/N] "
if /I "%ANS%"=="y" (
    python -m pip install deepface tf-keras || goto :error
)
echo.
echo Done. Start the app with run.bat
pause
exit /b 0
:error
echo Installation failed. See the messages above.
pause
exit /b 1
