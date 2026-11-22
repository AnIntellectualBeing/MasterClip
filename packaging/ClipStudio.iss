; Inno Setup script for Clip Studio. Built by packaging/build.ps1 (it passes /DSourceDir and /DOutDir).
#define AppName "MasterClip"
#define AppVersion "1.1.0"
#define AppExe "ClipStudio.exe"
#ifndef SourceDir
  #error Pass /DSourceDir=<PyInstaller "Clip Studio" folder>
#endif
#ifndef OutDir
  #define OutDir "..\dist"
#endif

[Setup]
AppId={{6C1E4F2A-3B7D-4C59-9A0E-5D2B8C7F1A30}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=AnIntellectualBeing
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir={#OutDir}
OutputBaseFilename=MasterClipSetup-{#AppVersion}
SetupIconFile=clipstudio.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
; ultra64 runs the 32-bit compiler out of memory on a ~700 MB app; max in its own process is fine.
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Put a MasterClip icon on my desktop"; GroupDescription: "Shortcuts:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "licences\*"; DestDir: "{app}\licences"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\MasterClip Help"; Filename: "{app}\_internal\static\help.html"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Open MasterClip now"; Flags: nowait postinstall skipifsilent

; Your projects, settings and saved keys stay when you uninstall (they live in %LOCALAPPDATA%\Clip Studio
; and Windows Credential Manager), so a reinstall picks up where you left off.
