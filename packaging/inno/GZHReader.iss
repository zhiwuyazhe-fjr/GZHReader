; Bundled wewe-rss now ships at top-level "r" instead of under "_internal" to
; shorten install paths. This keeps the package compatible with Inno Setup 6.7.1
; stable and avoids the uninstall regression we observed on Inno Setup 7 preview builds.
#define MyAppName "GZHReader"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif
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
Name: "{autodesktop}\GZHReader"; Filename: "{app}\GZHReader.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\GZHReader.exe"; Description: "Launch GZHReader"; Flags: nowait postinstall skipifsilent
