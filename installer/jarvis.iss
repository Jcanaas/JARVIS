; Inno Setup script for Jarvis (Mark-XXXIX)
; Build the PyInstaller one-folder output first (build/build.ps1), then compile:
;     ISCC.exe installer\jarvis.iss
; User data lives in %LOCALAPPDATA%\Jarvis (created at runtime), never here.

#define AppName "Jarvis"
#define AppVersion "1.0.0"
#define AppPublisher "Jordi Canadas"
#define AppExeName "Jarvis.exe"
; Folder produced by PyInstaller (dist\Jarvis)
#define DistDir "..\dist\Jarvis"

[Setup]
AppId={{B4D3F1A2-7C6E-4F2B-9E55-1A2B3C4D5E6F}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
OutputDir=..\dist\installer
OutputBaseFilename=Jarvis-Setup-{#AppVersion}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Per-machine install (Program Files); writable data goes to LocalAppData.
PrivilegesRequired=admin
SetupIconFile=mpv-icon.ico

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startupicon"; Description: "Iniciar Jarvis al arrancar Windows"; GroupDescription: "Inicio:"; Flags: unchecked

[Files]
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Desinstalar {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: startupicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Leave user data (%LOCALAPPDATA%\Jarvis) intact on uninstall by default.
Type: filesandordirs; Name: "{app}\_internal\__pycache__"
