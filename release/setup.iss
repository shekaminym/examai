[Setup]
AppId=EXAM-AI-Desktop
AppName=EXAM AI
AppVersion={#Version}
DefaultDirName={localappdata}\Programs\EXAM AI
DefaultGroupName=EXAM AI
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=beta-terms.txt
SetupIconFile=..\examai\assets\examai.ico
UninstallDisplayIcon={app}\EXAM AI.exe
OutputDir={#BuildPath}
OutputBaseFilename=EXAM-AI-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Tasks]
Name: desktopicon; Description: "Create a desktop shortcut"

[Files]
Source: "{#BuildPath}\dist\EXAM AI\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\EXAM AI"; Filename: "{app}\EXAM AI.exe"; AppUserModelID: ExamAI.Desktop
Name: "{autodesktop}\EXAM AI"; Filename: "{app}\EXAM AI.exe"; Tasks: desktopicon; AppUserModelID: ExamAI.Desktop

[Run]
Filename: "{app}\EXAM AI.exe"; Description: "Open EXAM AI"; Flags: nowait postinstall skipifsilent
