#define MyAppName "GZHReader"
#define MyAppVersion "0.2.0"
#define MyAppPublisher "zhiwuyazhe_fjr"
#define MyAppIcon "..\assets\gzhreader.ico"
#define WizardSidebarImage "..\assets\wizard-sidebar.bmp"
#define WizardSmallImage "..\assets\wizard-small.bmp"
#ifndef SourceDir
  #define SourceDir "dist\GZHReader"
#endif
#ifndef ReleaseDir
  #define ReleaseDir "release"
#endif

[Setup]
AppId={{2DCCB9D3-EBF8-4A56-BE52-43B22EAE4A58}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir={#ReleaseDir}
OutputBaseFilename=GZHReader-Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
SetupIconFile={#MyAppIcon}
WizardImageFile={#WizardSidebarImage}
WizardSmallImageFile={#WizardSmallImage}
UninstallDisplayIcon={app}\GZHReader.exe
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "Create desktop shortcut"; GroupDescription: "Additional tasks:";

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\GZHReader"; Filename: "{app}\GZHReader.exe"
Name: "{group}\GZHReader Console"; Filename: "{app}\GZHReader Console.exe"
Name: "{autodesktop}\GZHReader"; Filename: "{app}\GZHReader.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\GZHReader.exe"; Description: "Launch GZHReader"; Flags: nowait postinstall skipifsilent
