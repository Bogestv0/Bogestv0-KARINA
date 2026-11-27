; Inno Setup: iscc /DAppVersion=1.0.0 build\installer.iss  ->  dist\Bogestv0-KARINA-Setup-1.0.0.exe
; Мастер в тёмном стиле Карины: что поставить (права администратора, движок VPN, автозапуск), профиль по желанию,
; на последней странице выбор: ярлык на рабочий стол, запуск. Виджет-остров мастер не включает и не запускает: его включают в самой Карине (вкладка «Виджет»). Если Карина уже стоит, мастер работает как обновление:
; только короткая страница «Обновление» без вопросов, настройки, профиль, модели и голос сохраняются.
; Файл сохранён в UTF-8 с BOM: без него Inno Setup читает русский текст в кодировке ANSI и портит его.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppExe "Bogestv0-KARINA.exe"
#ifexist "..\vpn_engine\sing-box.exe"
  #define HasVpn
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
DisableWelcomePage=no
; без прав администратора: ставится в профиль пользователя. Права администратора для самой Карины выбираются на странице «Что установить»
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=commandline
OutputDir=..\dist
OutputBaseFilename=Bogestv0-KARINA-Setup-{#AppVersion}
SetupIconFile=..\ui\logo.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName=Bogestv0 KARINA
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
WizardSizePercent=120,120
WizardImageFile=wizard_side.bmp,wizard_side_2x.bmp
WizardSmallImageFile=wizard_small.bmp,wizard_small_2x.bmp
CloseApplications=yes
SetupMutex=Bogestv0KarinaSetup

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Files]
Source: "..\dist\Karina\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
#ifdef HasVpn
; движок VPN кладётся в папку данных Карины, туда же, куда он скачивается из настроек (karina/tunnel.py)
Source: "..\vpn_engine\sing-box.exe"; DestDir: "{userappdata}\Bogestv0 KARINA\tunnel"; Check: WantVpnEngine; Flags: ignoreversion
Source: "..\vpn_engine\VERSION.txt"; DestDir: "{userappdata}\Bogestv0 KARINA\tunnel"; Check: WantVpnEngine; Flags: ignoreversion
#endif

[Icons]
; ярлык в меню «Пуск» называется как программа: так её находит и сама Карина, и другие программы Bogestv0.
; Ярлык на рабочем столе создаёт последняя страница мастера (по желанию).
Name: "{autoprograms}\Bogestv0 KARINA"; Filename: "{app}\{#AppExe}"

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Bogestv0 KARINA"; ValueData: """{app}\{#AppExe}"" --background"; Check: WantAutostart; Flags: uninsdeletevalue

[UninstallDelete]
; настройки и модель распознавания (в AppData) НЕ удаляются: их можно убрать вручную, %APPDATA%\Bogestv0 KARINA
Type: filesandordirs; Name: "{app}"
Type: files; Name: "{autodesktop}\Bogestv0 KARINA.lnk"

[Code]
#ifdef HasVpn
const HAS_VPN = True;
#else
const HAS_VPN = False;
#endif

const
  UNINST_KEY = 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{B0C3A5E1-7D2F-4C8A-9E41-5A1D2F6B7C90}_is1';
  RUN_KEY = 'Software\Microsoft\Windows\CurrentVersion\Run';
  ADMIN_TASK = 'Bogestv0 KARINA';

var
  IsUpd: Boolean;
  OldVer: String;
  OptPage, ProfPage: TWizardPage;
  cbAdmin, cbVpn, cbAuto: TNewCheckBox;
  edName, edCity, edDay, edMonth, edYear: TNewEdit;
  fbDesktop, fbLaunch: TNewCheckBox;
  FinishBuilt, AdminOk: Boolean;
  ChkList: array[0..9] of TNewCheckBox;
  ChkCount: Integer;

{ ---------- цвета окна Карины (в Delphi цвет хранится как BGR) ---------- }
function Rgb(R, G, B: Integer): Integer;
begin
  Result := R + G * 256 + B * 65536;
end;

function ClBg: Integer;
begin
  Result := Rgb(19, 19, 24);
end;

function ClPanel: Integer;
begin
  Result := Rgb(23, 23, 28);
end;

function ClCard: Integer;
begin
  Result := Rgb(32, 32, 39);
end;

function ClFg: Integer;
begin
  Result := Rgb(242, 242, 246);
end;

function ClMuted: Integer;
begin
  Result := Rgb(154, 156, 174);
end;

function ClLine: Integer;
begin
  Result := Rgb(58, 58, 70);
end;

function ClAccent: Integer;
begin
  Result := Rgb(106, 179, 243);
end;

{ ---------- что уже стоит ---------- }
function InstalledVersion: String;
var
  V: String;
begin
  Result := '';
  if RegQueryStringValue(HKCU, UNINST_KEY, 'DisplayVersion', V) then
    Result := V
  else if RegQueryStringValue(HKLM, UNINST_KEY, 'DisplayVersion', V) then
    Result := V;
end;

{ ---------- проверки для [Files] и [Registry] ---------- }
function WantVpnEngine: Boolean;
begin
  Result := False;
  if HAS_VPN and (not IsUpd) then
    if cbVpn <> nil then
      Result := cbVpn.Checked;
end;

function WantAutostart: Boolean;
begin
  Result := False;
  if not IsUpd then
    if cbAuto <> nil then
      Result := cbAuto.Checked;
end;

{ ---------- оформление ---------- }
procedure ApplyTheme;
begin
  WizardForm.Color := ClBg;
  WizardForm.MainPanel.Color := ClPanel;
  WizardForm.InnerPage.Color := ClBg;
  WizardForm.WelcomePage.Color := ClBg;
  WizardForm.FinishedPage.Color := ClBg;
  WizardForm.SelectDirPage.Color := ClBg;
  WizardForm.ReadyPage.Color := ClBg;
  WizardForm.InstallingPage.Color := ClBg;
  WizardForm.Bevel.Visible := False;
  WizardForm.Bevel1.Visible := False;

  WizardForm.PageNameLabel.Font.Color := ClFg;
  WizardForm.PageNameLabel.Font.Size := 12;
  WizardForm.PageNameLabel.AutoSize := False;
  WizardForm.PageNameLabel.Top := ScaleY(6);
  WizardForm.PageNameLabel.Height := ScaleY(24);
  WizardForm.PageDescriptionLabel.Font.Color := ClMuted;
  WizardForm.PageDescriptionLabel.Left := WizardForm.PageNameLabel.Left + ScaleX(12);
  WizardForm.PageDescriptionLabel.Top := ScaleY(32);
  WizardForm.WelcomeLabel1.Font.Color := ClAccent;
  WizardForm.WelcomeLabel1.Font.Size := 17;
  WizardForm.WelcomeLabel2.Font.Color := ClFg;
  WizardForm.FinishedHeadingLabel.Font.Color := ClAccent;
  WizardForm.FinishedHeadingLabel.Font.Size := 17;
  WizardForm.FinishedLabel.Font.Color := ClFg;
  WizardForm.SelectDirLabel.Font.Color := ClFg;
  WizardForm.SelectDirBrowseLabel.Font.Color := ClMuted;
  WizardForm.DiskSpaceLabel.Font.Color := ClMuted;
  WizardForm.DirEdit.Color := ClCard;
  WizardForm.DirEdit.Font.Color := ClFg;
  WizardForm.ReadyLabel.Font.Color := ClFg;
  WizardForm.ReadyMemo.Color := ClCard;
  WizardForm.ReadyMemo.Font.Color := ClFg;
  WizardForm.StatusLabel.Font.Color := ClFg;
  WizardForm.FilenameLabel.Font.Color := ClMuted;
end;

{ Тема «modern» красит подпись флажка системным тёмным цветом, на тёмном фоне её не видно.
  Поэтому у флажка подпись пустая, а текст рисуется отдельной меткой нужного цвета (клик по метке переключает флажок). }
procedure ChkLabelClick(Sender: TObject);
var
  C: TNewCheckBox;
begin
  C := ChkList[TNewStaticText(Sender).Tag];
  if C.Enabled then
    C.Checked := not C.Checked;
end;

procedure SkinCheck(Cb: TNewCheckBox; Bold: Boolean);
var
  L: TNewStaticText;
begin
  L := TNewStaticText.Create(Cb.Parent);
  L.Parent := Cb.Parent;
  L.AutoSize := True;
  L.Left := Cb.Left + ScaleX(22);
  L.Top := Cb.Top + ScaleY(1);
  L.Caption := Cb.Caption;
  L.Font.Color := ClFg;
  if Bold then
    L.Font.Style := [fsBold];
  L.Cursor := crHand;
  L.Tag := ChkCount;
  L.OnClick := @ChkLabelClick;
  ChkList[ChkCount] := Cb;
  ChkCount := ChkCount + 1;
  Cb.Caption := '';
  Cb.Width := ScaleX(20);
end;

function AddOption(Page: TWizardPage; var Top: Integer; const Cap, Desc: String; Checked: Boolean): TNewCheckBox;
var
  T: TNewStaticText;
begin
  Result := TNewCheckBox.Create(Page);
  Result.Parent := Page.Surface;
  Result.Left := 0;
  Result.Top := Top;
  Result.Width := Page.SurfaceWidth;
  Result.Height := ScaleY(18);
  Result.Caption := Cap;
  Result.Checked := Checked;
  SkinCheck(Result, True);
  T := TNewStaticText.Create(Page);
  T.Parent := Page.Surface;
  T.AutoSize := False;
  T.WordWrap := True;
  T.Left := ScaleX(22);
  T.Top := Top + ScaleY(20);
  T.Width := Page.SurfaceWidth - ScaleX(22);
  T.Height := ScaleY(30);
  T.Caption := Desc;
  T.Font.Color := ClMuted;
  Top := Top + ScaleY(60);
end;

{ Поле ввода в стиле Карины: тёмная карточка без белой системной рамки и подпись над полем. Рамка тонкая серая, при вводе голубая. }
procedure EditFocus(Sender: TObject);
begin
  TPanel(TNewEdit(Sender).Parent.Parent).Color := ClAccent;
end;

procedure EditBlur(Sender: TObject);
begin
  TPanel(TNewEdit(Sender).Parent.Parent).Color := ClLine;
end;

function AddField(Page: TWizardPage; ALeft, ATop, AWidth: Integer; const Cap: String): TNewEdit;
var
  L: TNewStaticText;
  Box, Fill: TPanel;
begin
  L := TNewStaticText.Create(Page);
  L.Parent := Page.Surface;
  L.Caption := Cap;
  L.Left := ALeft;
  L.Top := ATop;
  L.Font.Color := ClMuted;
  Box := TPanel.Create(Page);
  Box.Parent := Page.Surface;
  Box.BevelOuter := bvNone;
  Box.ParentBackground := False;
  Box.Color := ClLine;
  Box.Left := ALeft;
  Box.Top := ATop + ScaleY(20);
  Box.Width := AWidth;
  Box.Height := ScaleY(32);
  Fill := TPanel.Create(Page);
  Fill.Parent := Box;
  Fill.BevelOuter := bvNone;
  Fill.ParentBackground := False;
  Fill.Color := ClCard;
  Fill.Left := 1;
  Fill.Top := 1;
  Fill.Width := AWidth - 2;
  Fill.Height := ScaleY(32) - 2;
  Result := TNewEdit.Create(Page);
  Result.Parent := Fill;
  Result.BorderStyle := bsNone;
  Result.Color := ClCard;
  Result.Font.Color := ClFg;
  Result.Font.Size := 10;
  Result.Left := ScaleX(10);
  Result.Width := AWidth - 2 - ScaleX(20);
  Result.Top := ScaleY(6);
  Result.OnEnter := @EditFocus;
  Result.OnExit := @EditBlur;
end;

{ ---------- страницы ---------- }
procedure BuildOptionsPage;
var
  Top: Integer;
begin
  OptPage := CreateCustomPage(wpWelcome, 'Что установить',
    'Отметьте нужное. Всё это можно поменять потом в настройках Карины.');
  OptPage.Surface.Color := ClBg;
  Top := 0;
  cbAdmin := AddOption(OptPage, Top, 'Запускать Карину с правами администратора',
    'Нужно, чтобы управлять окнами программ, запущенных от администратора, и для VPN на весь компьютер. Windows один раз спросит разрешение.', False);
  if HAS_VPN then
    cbVpn := AddOption(OptPage, Top, 'Сразу установить движок для VPN (sing-box)',
      'Около 12 МБ, ставится вместе с Кариной. Серверы или подписку добавите потом в настройках VPN.', True)
  else
    cbVpn := nil;
  cbAuto := AddOption(OptPage, Top, 'Запускать Карину вместе с Windows',
    'Стартует тихо, в трее, окно не открывается: Карина просто ждёт своё имя.', False);
end;

procedure BuildProfilePage;
var
  W, Gap, Cell: Integer;
  H: TNewStaticText;
begin
  ProfPage := CreateCustomPage(OptPage.ID, 'Немного о вас',
    'Необязательно. Для обращения по имени, погоды и поздравлений.');
  ProfPage.Surface.Color := ClBg;
  W := ProfPage.SurfaceWidth;
  edName := AddField(ProfPage, 0, 0, W, 'Как к вам обращаться');
  edCity := AddField(ProfPage, 0, ScaleY(66), W, 'Город (для погоды)');
  { день рождения: общая подпись, а под ней три поля со своими короткими подписями — ничего не налезает друг на друга }
  H := TNewStaticText.Create(ProfPage);
  H.Parent := ProfPage.Surface;
  H.Caption := 'День рождения';
  H.Left := 0;
  H.Top := ScaleY(132);
  H.Font.Color := ClMuted;
  Gap := ScaleX(12);
  Cell := ScaleX(78);
  edDay := AddField(ProfPage, 0, ScaleY(152), Cell, 'день');
  edMonth := AddField(ProfPage, Cell + Gap, ScaleY(152), Cell, 'месяц');
  edYear := AddField(ProfPage, (Cell + Gap) * 2, ScaleY(152), ScaleX(96), 'год');
  edDay.MaxLength := 2;
  edMonth.MaxLength := 2;
  edYear.MaxLength := 4;
  edName.MaxLength := 40;
  edCity.MaxLength := 60;
end;

function ValidProfile: Boolean;
var
  D, M, Y: Integer;
begin
  Result := True;
  D := StrToIntDef(Trim(edDay.Text), 0);
  M := StrToIntDef(Trim(edMonth.Text), 0);
  Y := StrToIntDef(Trim(edYear.Text), 0);
  if ((Trim(edDay.Text) <> '') and ((D < 1) or (D > 31))) or
     ((Trim(edMonth.Text) <> '') and ((M < 1) or (M > 12))) or
     ((Trim(edYear.Text) <> '') and ((Y < 1900) or (Y > 2100))) then
  begin
    MsgBox('Проверьте дату рождения: день от 1 до 31, месяц от 1 до 12, год четырьмя цифрами. Или оставьте поля пустыми.', mbError, MB_OK);
    Result := False;
  end;
end;

procedure BuildFinish;
var
  Top: Integer;
begin
  if FinishBuilt then
    Exit;
  FinishBuilt := True;
  Top := WizardForm.FinishedLabel.Top + WizardForm.FinishedLabel.Height + ScaleY(14);
  if not IsUpd then
  begin
    fbDesktop := TNewCheckBox.Create(WizardForm);
    fbDesktop.Parent := WizardForm.FinishedPage;
    fbDesktop.Left := WizardForm.FinishedLabel.Left;
    fbDesktop.Top := Top;
    fbDesktop.Width := WizardForm.FinishedLabel.Width;
    fbDesktop.Height := ScaleY(20);
    fbDesktop.Caption := 'Добавить Карину на рабочий стол';
    fbDesktop.Checked := True;
    SkinCheck(fbDesktop, False);
    Top := Top + ScaleY(26);
  end;
  fbLaunch := TNewCheckBox.Create(WizardForm);
  fbLaunch.Parent := WizardForm.FinishedPage;
  fbLaunch.Left := WizardForm.FinishedLabel.Left;
  fbLaunch.Top := Top;
  fbLaunch.Width := WizardForm.FinishedLabel.Width;
  fbLaunch.Height := ScaleY(20);
  fbLaunch.Caption := 'Запустить Карину';
  fbLaunch.Checked := True;
  SkinCheck(fbLaunch, False);
end;

{ ---------- события мастера ---------- }
procedure InitializeWizard;
begin
  OldVer := InstalledVersion;
  IsUpd := OldVer <> '';
  ApplyTheme;
  if IsUpd then
  begin
    WizardForm.WelcomeLabel1.Caption := 'Обновление Карины';
    WizardForm.WelcomeLabel2.Caption := 'Сейчас стоит версия ' + OldVer + ', будет ' + '{#AppVersion}' + '.' + #13#10 + #13#10 +
      'Настройки, профиль, модели и голос сохранятся. Ничего выбирать не нужно: нажмите «Обновить».';
  end
  else
  begin
    WizardForm.WelcomeLabel1.Caption := 'Добро пожаловать в Карину';
    WizardForm.WelcomeLabel2.Caption := 'Голосовой помощник для Windows: понимает речь на вашем компьютере, открывает программы, ведёт чат, ' +
      'управляет звуком и VPN. Голос Карины уже внутри, ничего скачивать не придётся.' + #13#10 + #13#10 +
      'Дальше вы выберете, что установить. Это займёт минуту.';
  end;
  BuildOptionsPage;
  BuildProfilePage;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  if IsUpd then
    if (PageID = OptPage.ID) or (PageID = ProfPage.ID) or (PageID = wpSelectDir) or (PageID = wpReady) then
      Result := True;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if CurPageID = ProfPage.ID then
    Result := ValidProfile;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if (CurPageID = wpWelcome) and IsUpd then
    WizardForm.NextButton.Caption := 'Обновить';
  if CurPageID = wpFinished then
  begin
    if IsUpd then
    begin
      WizardForm.FinishedHeadingLabel.Caption := 'Карина обновлена';
      WizardForm.FinishedLabel.Caption := 'Версия {#AppVersion} установлена. Ваши настройки на месте.';
    end
    else
    begin
      WizardForm.FinishedHeadingLabel.Caption := 'Готово!';
      WizardForm.FinishedLabel.Caption := 'Карина установлена. Выберите, что сделать сразу.';
    end;
    BuildFinish;
  end;
end;

function Indent(const Space, Text: String): String;
begin
  Result := Space + Text;
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
var
  S: String;
begin
  S := MemoDirInfo + NewLine + NewLine + 'Что будет сделано:';
  S := S + NewLine + Indent(Space, 'Карина и голос Карины: ставятся всегда');
  if cbAdmin.Checked then
    S := S + NewLine + Indent(Space, 'Запуск с правами администратора (Windows спросит разрешение)');
  if WantVpnEngine then
    S := S + NewLine + Indent(Space, 'Движок VPN (sing-box)');
  if cbAuto.Checked then
    S := S + NewLine + Indent(Space, 'Автозапуск Карины вместе с Windows');
  if Trim(edName.Text) <> '' then
    S := S + NewLine + NewLine + 'Профиль: ' + Trim(edName.Text) + ' ' + Trim(edCity.Text);
  Result := S;
end;

{ ---------- действия после копирования файлов ---------- }
function JsonText(const S: String): String;
var
  T: String;
begin
  T := S;
  StringChangeEx(T, '\', '\\', True);
  StringChangeEx(T, '"', '\"', True);
  Result := T;
end;

function BoolText(B: Boolean): String;
begin
  if B then
    Result := 'true'
  else
    Result := 'false';
end;

{ Карина при первом запуске переносит этот файл в свои настройки и удаляет его (karina/install_options.py). }
procedure WriteOptions;
var
  S, Dir: String;
  A: TArrayOfString;
begin
  S := '{"ui":{"island":false';
  if AdminOk then
    S := S + ',"admin":true';
  S := S + '},"profile":{"name":"' + JsonText(Trim(edName.Text)) + '","city":"' + JsonText(Trim(edCity.Text)) + '"' +
    ',"birth_day":' + IntToStr(StrToIntDef(Trim(edDay.Text), 0)) +
    ',"birth_month":' + IntToStr(StrToIntDef(Trim(edMonth.Text), 0)) +
    ',"birth_year":' + IntToStr(StrToIntDef(Trim(edYear.Text), 0)) + '}}';
  Dir := ExpandConstant('{userappdata}\Bogestv0 KARINA');
  ForceDirectories(Dir);
  SetArrayLength(A, 1);
  A[0] := S;
  SaveStringsToUTF8File(Dir + '\install_options.json', A, False);
end;

procedure MakeDesktopLink(const Exe: String);
var
  Sh, Lnk: Variant;
begin
  try
    Sh := CreateOleObject('WScript.Shell');
    Lnk := Sh.CreateShortcut(ExpandConstant('{autodesktop}\Bogestv0 KARINA.lnk'));
    Lnk.TargetPath := Exe;
    Lnk.WorkingDirectory := ExpandConstant('{app}');
    Lnk.Description := 'Bogestv0 KARINA';
    Lnk.Save;
  except
    Log('Не удалось создать ярлык на рабочем столе');
  end;
end;

procedure SetupAdminTask;
var
  Code: Integer;
begin
  { задача планировщика с наивысшими правами создаётся самой Кариной; UAC спросит один раз }
  AdminOk := False;
  if ShellExec('runas', ExpandConstant('{app}\{#AppExe}'), '--install-admin-task', '', SW_HIDE, ewWaitUntilTerminated, Code) then
    AdminOk := (Code = 0);
  if not AdminOk then
    Log('Права администратора для Карины не включены (отказ в UAC или ошибка)');
end;

procedure FinishUp;
var
  Exe: String;
  Launch, Desktop: Boolean;
  Code: Integer;
begin
  Exe := ExpandConstant('{app}\{#AppExe}');
  if IsUpd then
  begin
    if (not WizardSilent) and FinishBuilt and fbLaunch.Checked then
      ExecAsOriginalUser(Exe, '', '', SW_SHOWNORMAL, ewNoWait, Code);
    Exit;
  end;
  Launch := False;
  Desktop := False;
  if (not WizardSilent) and FinishBuilt then
  begin
    Desktop := fbDesktop.Checked;
    Launch := fbLaunch.Checked;
  end;
  WriteOptions;
  if Desktop then
    MakeDesktopLink(Exe);
  { виджет-остров мастер не включает и сам не запускает: его включают в Карине (вкладка «Виджет») }
  if Launch then
    ExecAsOriginalUser(Exe, '', '', SW_SHOWNORMAL, ewNoWait, Code);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and (not IsUpd) and (not WizardSilent) and cbAdmin.Checked then
    SetupAdminTask;
  if CurStep = ssDone then
    FinishUp;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    Exec(ExpandConstant('{sys}\schtasks.exe'), '/Delete /TN "' + ADMIN_TASK + '" /F', '', SW_HIDE, ewWaitUntilTerminated, Code);
    RegDeleteValue(HKCU, RUN_KEY, 'Bogestv0 KARINA');
  end;
end;
