; Inno Setup script for the ClassGuard Windows installer.
; Build:  iscc /DAppVersion=2.0.0 packaging\installer.iss
; Expects the PyInstaller output in dist\ClassGuard\ (run: pyinstaller classguard.spec)

#ifndef AppVersion
  #define AppVersion "2.0.6"
#endif

[Setup]
AppId={{6B7C3E2A-5D1F-4C8B-9E3A-C1A55C1A55C1}
AppName=ClassGuard
AppVersion={#AppVersion}
AppPublisher=ClassGuard
DefaultDirName={autopf}\ClassGuard
DefaultGroupName=ClassGuard
UninstallDisplayIcon={app}\ClassGuard.exe
OutputDir=..\dist
OutputBaseFilename=ClassGuard-Setup-{#AppVersion}
SetupIconFile=..\app\assets\icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"
Name: "autostart"; Description: "Start ClassGuard when I sign in to Windows (recommended for 24x7 recording)"; GroupDescription: "Startup:"; Flags: unchecked

[Files]
Source: "..\dist\ClassGuard\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\ClassGuard"; Filename: "{app}\ClassGuard.exe"
Name: "{group}\ClassGuard self-test"; Filename: "{app}\ClassGuard.exe"; Parameters: "--self-test"; WorkingDir: "{userdocs}"
Name: "{group}\Uninstall ClassGuard"; Filename: "{uninstallexe}"
Name: "{autodesktop}\ClassGuard"; Filename: "{app}\ClassGuard.exe"; Tasks: desktopicon
Name: "{userstartup}\ClassGuard"; Filename: "{app}\ClassGuard.exe"; Tasks: autostart

[Run]
Filename: "{app}\ClassGuard.exe"; Description: "Start ClassGuard now"; Flags: nowait postinstall skipifsilent

; Cameras, recordings and settings live in %USERPROFILE%\.classguard and are
; deliberately kept when uninstalling.
