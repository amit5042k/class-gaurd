@echo off
rem Builds dist\ClassGuard\ClassGuard.exe and, if Inno Setup is installed,
rem dist\ClassGuard-Setup-<version>.exe
cd /d "%~dp0"
if not exist .venv-build python -m venv .venv-build || goto :error
call .venv-build\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller || goto :error
python -m pip install "setuptools<81" dlib-bin face_recognition_models Click Pillow || goto :error
python -m pip install --no-deps face_recognition || goto :error
pyinstaller classguard.spec --noconfirm || goto :error
dist\ClassGuard\ClassGuard.exe --self-test
type classguard-selftest.txt
set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist %ISCC% (
    %ISCC% packaging\installer.iss || goto :error
    echo Installer written to the dist folder.
) else (
    echo Inno Setup 6 not found: skipped the installer. The app is in dist\ClassGuard\
)
exit /b 0
:error
echo Build failed.
exit /b 1
