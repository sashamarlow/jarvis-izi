# All downloaded components stay inside this application's directory.
$ErrorActionPreference = 'Stop'
$appRoot = $PSScriptRoot
$runtimeRoot = Join-Path $appRoot '.runtime'
$noPause = $args -contains '--no-pause'
$setupOnly = $args -contains '--setup-check'
$forcePortable = $args -contains '--portable-runtime'
$appArguments = @($args | Where-Object { $_ -notin @('--no-pause', '--setup-check', '--portable-runtime') })
$exitCode = 1
$setupLock = $null

[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$env:NO_COLOR = $null
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Get-VerifiedDownload($specification, [string]$destination) {
    $destination = [IO.Path]::GetFullPath($destination)
    if (-not $destination.StartsWith($runtimeRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Путь загрузки должен оставаться внутри .runtime.'
    }
    $uri = [Uri]$specification.url
    if ($uri.Scheme -ne 'https' -or $uri.Host -notin @('www.python.org', 'bootstrap.pypa.io')) {
        throw 'Недопустимый адрес загрузки.'
    }
    if ((Test-Path -LiteralPath $destination) -and
        (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -eq $specification.sha256) {
        return
    }
    $partial = $destination + '.part'
    Invoke-WebRequest -Uri $uri.AbsoluteUri -OutFile $partial -UseBasicParsing -TimeoutSec 180
    if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ne $specification.sha256) {
        throw 'Проверка загруженного файла не пройдена. Файл не будет запущен.'
    }
    Move-Item -LiteralPath $partial -Destination $destination -Force
}

function Test-AppDependencies([string]$python) {
    if (-not (Test-Path -LiteralPath $python)) { return $false }
    $check = @'
import importlib.metadata as m
from pathlib import Path
import sys
try:
    import playwright.async_api, greenlet, pyee
    requirements = Path(sys.argv[1]).read_text().splitlines()
    assert all(m.version(line.split('==')[0]) == line.split('==')[1] for line in requirements if line.strip())
except Exception:
    sys.exit(1)
'@
    & $python -X utf8 -c $check (Join-Path $appRoot 'requirements.txt') 1>$null 2>$null
    return $LASTEXITCODE -eq 0
}

try {
    if (-not [Environment]::Is64BitOperatingSystem -or [Environment]::OSVersion.Version.Major -lt 10) {
        throw 'Поддерживаются Windows 10/11 x64.'
    }
    $architecture = $env:PROCESSOR_ARCHITEW6432
    if (-not $architecture) { $architecture = $env:PROCESSOR_ARCHITECTURE }
    if ($architecture -ne 'AMD64') { throw 'Этот выпуск предназначен для Windows x64, не ARM64 или x86.' }

    $edgePaths = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\Edge\Application\msedge.exe')
    )
    if (-not ($edgePaths | Where-Object { Test-Path -LiteralPath $_ })) {
        throw 'Не найден Microsoft Edge. Установи его с https://www.microsoft.com/edge и повтори запуск.'
    }

    New-Item -ItemType Directory -Path $runtimeRoot -Force | Out-Null
    try {
        $setupLock = [IO.File]::Open((Join-Path $runtimeRoot 'setup.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    } catch {
        throw 'Подготовка уже запущена в другом окне. Дождись её завершения.'
    }
    Set-Location -LiteralPath $appRoot
    $python = Join-Path $appRoot '.venv\Scripts\python.exe'
    if ($forcePortable -or -not (Test-AppDependencies $python)) {
        # A parent PowerShell 7 process can pass its module search path through CMD.
        # Load this Windows PowerShell's built-ins explicitly, without changing PATH.
        Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
        Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Archive\Microsoft.PowerShell.Archive.psd1') -ErrorAction Stop
        $pythonRoot = Join-Path $runtimeRoot 'python'
        $python = Join-Path $pythonRoot 'python.exe'
        if (-not (Test-Path -LiteralPath $python)) {
            Write-Host '[SETUP] Первый запуск: скачиваю локальный Python...' -ForegroundColor Yellow
            $downloads = Get-Content -LiteralPath (Join-Path $appRoot 'downloads.json') -Raw | ConvertFrom-Json
            $archive = Join-Path $runtimeRoot 'python.zip'
            Get-VerifiedDownload $downloads.python $archive
            Expand-Archive -LiteralPath $archive -DestinationPath $pythonRoot -Force
        }
        # Explicit paths keep imports local and include the application itself.
        $pth = "python314.zip`r`n.`r`nLib\site-packages`r`n..\..`r`nimport site`r`n"
        [IO.File]::WriteAllText((Join-Path $pythonRoot 'python314._pth'), $pth, [Text.UTF8Encoding]::new($false))
        if (-not (Test-AppDependencies $python)) {
            Write-Host '[SETUP] Устанавливаю библиотеки программы. Нужен интернет...' -ForegroundColor Yellow
            $downloads = Get-Content -LiteralPath (Join-Path $appRoot 'downloads.json') -Raw | ConvertFrom-Json
            $pip = Join-Path $runtimeRoot 'pip.pyz'
            Get-VerifiedDownload $downloads.pip $pip
            & $python -X utf8 $pip install --isolated --only-binary=:all: --disable-pip-version-check --no-cache-dir --no-warn-script-location --upgrade --target (Join-Path $pythonRoot 'Lib\site-packages') -r (Join-Path $appRoot 'requirements.txt')
            if ($LASTEXITCODE -ne 0 -or -not (Test-AppDependencies $python)) {
                throw 'Библиотеки не установлены. Проверь интернет/VPN и повтори запуск.'
            }
        }
    }
    $setupLock.Dispose()
    $setupLock = $null
    if ($setupOnly) {
        # Never import the bot or read a Telegram profile in this branch.
        Write-Host '[OK] Подготовка проверена. Telegram не подключался.' -ForegroundColor Green
        $exitCode = 0
    } else {
        & $python -X utf8 (Join-Path $appRoot 'web_app.py') @appArguments
        $exitCode = $LASTEXITCODE
    }
} catch {
    Write-Host ('[ERROR] ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Запускай из полностью распакованной папки, не из ZIP. Проверь интернет и доступ к Telegram.'
    $exitCode = 1
} finally {
    if ($setupLock) { $setupLock.Dispose() }
    if (-not $noPause) {
        Write-Host 'Нажми Enter, чтобы закрыть окно.'
        try { [void](Read-Host) } catch { }
    }
}
exit $exitCode
