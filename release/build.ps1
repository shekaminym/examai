param([string]$Version = "3.0.0-beta.1",
      [string]$BuildRoot = "release-build",
      [switch]$SkipInstaller)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot)
$buildPath = Join-Path (Get-Location) $BuildRoot
python -m PyInstaller --noconfirm --onedir --windowed --name "EXAM AI" --icon "$PWD/examai/assets/examai.ico" --add-data "$PWD/examai/assets;examai/assets" --add-data "$PWD/examai/audio/speech_glossary.json;examai/audio" --exclude-module torch --exclude-module tensorflow --exclude-module IPython --exclude-module pytest --exclude-module pandas --collect-data faster_whisper --collect-binaries onnxruntime --collect-binaries ctranslate2 --distpath "$buildPath/dist" --workpath "$buildPath/work" --specpath $buildPath main.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
if (!$SkipInstaller) {
    $iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    $compiler = if ($iscc) { $iscc.Source } else {
        @("${env:LOCALAPPDATA}/Programs/Inno Setup 6/ISCC.exe", "${env:ProgramFiles(x86)}/Inno Setup 6/ISCC.exe", "$env:ProgramFiles/Inno Setup 6/ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    }
    if (!$compiler) { throw "Install Inno Setup 6 or run with -SkipInstaller" }
    & $compiler "/DVersion=$Version" "/DBuildPath=$buildPath" "$PSScriptRoot/setup.iss"
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
}
