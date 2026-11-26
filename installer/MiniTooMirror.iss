; MiniToo Mirror - Windows installer (Inno Setup 6)
; Build: build.bat  (or: ISCC.exe /DAppVersion=1.0.0 installer\MiniTooMirror.iss)

#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#define AppName "MiniToo Mirror"
#define AppExe "MiniTooMirror.exe"
#define RepoUrl "https://github.com/nikadxxdxgod/divoom-minitoo-screen-mirror"

[Setup]
AppId={{6F3B2C8E-4A1D-4E7B-9C55-2D8E1A7F0B31}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=MiniToo Mirror contributors
AppPublisherURL={#RepoUrl}
AppSupportURL={#RepoUrl}/issues
AppUpdatesURL={#RepoUrl}/releases
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\dist
OutputBaseFilename=MiniTooMirror-Setup-{#AppVersion}
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
LicenseFile=..\LICENSE
WizardStyle=modern
WizardImageFile=wizard-large.bmp
WizardSmallImageFile=wizard-small.bmp
WizardImageStretch=yes
ShowLanguageDialog=yes
Compression=lzma2/ultra64
SolidCompression=yes
CloseApplications=yes
VersionInfoVersion={#AppVersion}
VersionInfoDescription={#AppName} Setup

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"
Name: "french"; MessagesFile: "compiler:Languages\French.isl"
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "polish"; MessagesFile: "compiler:Languages\Polish.isl"
Name: "turkish"; MessagesFile: "compiler:Languages\Turkish.isl"
Name: "ukrainian"; MessagesFile: "compiler:Languages\Ukrainian.isl"
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[CustomMessages]
english.LangTitle=Program language
english.LangDesc=Choose the language of MiniToo Mirror.
english.LangHint=You can change it later in the program's Settings.
german.LangTitle=Programmsprache
german.LangDesc=Wählen Sie die Sprache von MiniToo Mirror.
german.LangHint=Sie können sie später in den Einstellungen des Programms ändern.
french.LangTitle=Langue du programme
french.LangDesc=Choisissez la langue de MiniToo Mirror.
french.LangHint=Vous pourrez la changer plus tard dans les Paramètres du programme.
spanish.LangTitle=Idioma del programa
spanish.LangDesc=Elija el idioma de MiniToo Mirror.
spanish.LangHint=Puede cambiarlo más tarde en la Configuración del programa.
italian.LangTitle=Lingua del programma
italian.LangDesc=Scegli la lingua di MiniToo Mirror.
italian.LangHint=Potrai cambiarla più tardi nelle Impostazioni del programma.
brazilianportuguese.LangTitle=Idioma do programa
brazilianportuguese.LangDesc=Escolha o idioma do MiniToo Mirror.
brazilianportuguese.LangHint=Você pode mudá-lo depois nas Configurações do programa.
polish.LangTitle=Język programu
polish.LangDesc=Wybierz język programu MiniToo Mirror.
polish.LangHint=Możesz go później zmienić w Ustawieniach programu.
turkish.LangTitle=Program dili
turkish.LangDesc=MiniToo Mirror'ın dilini seçin.
turkish.LangHint=Daha sonra programın Ayarlar bölümünden değiştirebilirsiniz.
ukrainian.LangTitle=Мова програми
ukrainian.LangDesc=Оберіть мову MiniToo Mirror.
ukrainian.LangHint=Її можна змінити пізніше в налаштуваннях програми.
russian.LangTitle=Язык программы
russian.LangDesc=Выберите язык MiniToo Mirror.
russian.LangHint=Его можно изменить позже в настройках программы.
japanese.LangTitle=プログラムの言語
japanese.LangDesc=MiniToo Mirror の言語を選択してください。
japanese.LangHint=あとでプログラムの設定から変更できます。
korean.LangTitle=프로그램 언어
korean.LangDesc=MiniToo Mirror의 언어를 선택하세요.
korean.LangHint=나중에 프로그램 설정에서 바꿀 수 있습니다.
english.DiagName=MiniToo Mirror diagnostics
german.DiagName=MiniToo Mirror Diagnose
french.DiagName=Diagnostic MiniToo Mirror
spanish.DiagName=Diagnóstico de MiniToo Mirror
italian.DiagName=Diagnostica MiniToo Mirror
brazilianportuguese.DiagName=Diagnóstico do MiniToo Mirror
polish.DiagName=Diagnostyka MiniToo Mirror
turkish.DiagName=MiniToo Mirror tanılama
ukrainian.DiagName=Діагностика MiniToo Mirror
russian.DiagName=Диагностика MiniToo Mirror
japanese.DiagName=MiniToo Mirror 診断
korean.DiagName=MiniToo Mirror 진단

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\MiniTooMirror\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autoprograms}\{cm:DiagName}"; Filename: "{app}\MiniTooDiagnose.exe"; WorkingDir: "{userdocs}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\MiniTooMirror"; Flags: uninsdeletekeyifempty
Root: HKCU; Subkey: "Software\MiniTooMirror"; ValueType: string; ValueName: "Language"; ValueData: "{code:GetLangCode}"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
var
  LangPage: TWizardPage;
  LangList: TNewListBox;

const
  LangCount = 14;

function LangCodeAt(i: Integer): String;
begin
  case i of
    0: Result := 'en';  1: Result := 'ka';  2: Result := 'de';  3: Result := 'fr';
    4: Result := 'es';  5: Result := 'it';  6: Result := 'pt';  7: Result := 'pl';
    8: Result := 'tr';  9: Result := 'uk'; 10: Result := 'ru'; 11: Result := 'zh';
    12: Result := 'ja'; 13: Result := 'ko';
  else
    Result := 'en';
  end;
end;

function LangNameAt(i: Integer): String;
begin
  case i of
    0: Result := 'English';
    1: Result := 'ქართული — Georgian';
    2: Result := 'Deutsch — German';
    3: Result := 'Français — French';
    4: Result := 'Español — Spanish';
    5: Result := 'Italiano — Italian';
    6: Result := 'Português (Brasil) — Portuguese';
    7: Result := 'Polski — Polish';
    8: Result := 'Türkçe — Turkish';
    9: Result := 'Українська — Ukrainian';
    10: Result := 'Русский — Russian';
    11: Result := '简体中文 — Chinese (Simplified)';
    12: Result := '日本語 — Japanese';
    13: Result := '한국어 — Korean';
  else
    Result := 'English';
  end;
end;

function DefaultLangIndex: Integer;
var
  L: String;
  Prim: Integer;
begin
  { 1) Windows UI language for languages the installer itself has no UI for }
  Prim := GetUILanguage and $3FF;
  if Prim = $37 then begin Result := 1; exit; end;      { Georgian }
  if Prim = $04 then begin Result := 11; exit; end;     { Chinese }
  { 2) otherwise the installer language }
  L := ActiveLanguage;
  if L = 'german' then Result := 2
  else if L = 'french' then Result := 3
  else if L = 'spanish' then Result := 4
  else if L = 'italian' then Result := 5
  else if L = 'brazilianportuguese' then Result := 6
  else if L = 'polish' then Result := 7
  else if L = 'turkish' then Result := 8
  else if L = 'ukrainian' then Result := 9
  else if L = 'russian' then Result := 10
  else if L = 'japanese' then Result := 12
  else if L = 'korean' then Result := 13
  else Result := 0;
end;

procedure InitializeWizard;
var
  i: Integer;
  Hint: TNewStaticText;
begin
  LangPage := CreateCustomPage(wpSelectTasks, CustomMessage('LangTitle'), CustomMessage('LangDesc'));
  LangList := TNewListBox.Create(LangPage);
  LangList.Parent := LangPage.Surface;
  LangList.Left := 0;
  LangList.Top := 0;
  LangList.Width := LangPage.SurfaceWidth;
  LangList.Height := LangPage.SurfaceHeight - ScaleY(28);
  for i := 0 to LangCount - 1 do
    LangList.Items.Add(LangNameAt(i));
  LangList.ItemIndex := DefaultLangIndex;
  Hint := TNewStaticText.Create(LangPage);
  Hint.Parent := LangPage.Surface;
  Hint.Top := LangList.Top + LangList.Height + ScaleY(8);
  Hint.Caption := CustomMessage('LangHint');
end;

function GetLangCode(Param: String): String;
begin
  if (LangList <> nil) and (LangList.ItemIndex >= 0) then
    Result := LangCodeAt(LangList.ItemIndex)
  else
    Result := LangCodeAt(DefaultLangIndex);
end;
