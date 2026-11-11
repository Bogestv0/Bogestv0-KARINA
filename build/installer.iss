; Inno Setup: iscc /DAppVersion=1.0.0 build\installer.iss  →  dist\Bogestv0-KARINA-Setup-1.0.0.exe
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{B0C3A5E1-7D2F-4C8A-9E41-5A1D2F6B7C90}
AppName=Bogestv0 KARINA
AppVersion={#AppVersion}
AppPublisher=Bogestv0
AppPublisherURL=https://t.me/bogestv0
DefaultDirName={autopf}\Bogestv0 KARINA
DefaultGroupName=Bogestv0 KARINA
DisableProgramGroupPage=yes
; без прав администратора: ставится в профиль пользователя, если не выбрано иное
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=Bogestv0-KARINA-Setup-{#AppVersion}
SetupIconFile=..\ui\logo.ico
UninstallDisplayIcon={app}\Bogestv0-KARINA.exe
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Значок на рабочем столе"; GroupDescription: "Ярлыки:"
Name: "autostart"; Description: "Запускать вместе с Windows"; GroupDescription: "Запуск:"; Flags: unchecked

[Files]
Source: "..\dist\Karina\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; ярлык в меню «Пуск» называется как программа — так её находит и сама Карина, и другие программы Bogestv0
Name: "{autoprograms}\Bogestv0 KARINA"; Filename: "{app}\Bogestv0-KARINA.exe"
Name: "{autodesktop}\Bogestv0 KARINA"; Filename: "{app}\Bogestv0-KARINA.exe"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Bogestv0 KARINA"; ValueData: """{app}\Bogestv0-KARINA.exe"" --background"; Tasks: autostart; Flags: uninsdeletevalue

[Run]
Filename: "{app}\Bogestv0-KARINA.exe"; Description: "Запустить Bogestv0 KARINA"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; настройки и модель распознавания (в AppData) НЕ удаляются — их можно убрать вручную: %APPDATA%\Bogestv0 KARINA
Type: filesandordirs; Name: "{app}"
