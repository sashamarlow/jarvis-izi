param([Parameter(Mandatory=$true)][string]$Destination)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$destinationPath = [IO.Path]::GetFullPath($Destination)
if (Test-Path -LiteralPath $destinationPath) { throw 'Archive already exists; choose a new filename.' }
if ($destinationPath.StartsWith($projectRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Choose an archive destination outside the working project.'
}
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$files = @('start.cmd', 'app/web_app.py', 'app/web_dom.js', 'app/console_ui.py',
    'app/core.py', 'app/rules.py', 'app/runtime.py', 'app/author_settings.py', 'app/rules.json',
    'app/requirements.txt', 'app/downloads.json', 'app/launch.ps1',
    'app/package_release.ps1', 'app/README.md', 'app/BENCHMARK.md', 'app/.gitignore', 'app/.gitattributes',
    'app/tests/test_core.py', 'app/tests/test_rules.py', 'app/tests/test_console_ui.py',
    'app/tests/test_launchers.py', 'app/tests/test_authors.py', 'app/tests/test_web.py', 'app/tests/benchmark_web.py')
foreach ($relativePath in $files) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $relativePath) -PathType Leaf)) {
        throw "Missing release file: $relativePath"
    }
}
$stream = [IO.File]::Open($destinationPath, 'CreateNew', 'ReadWrite', 'None')
try {
    $zip = [IO.Compression.ZipArchive]::new($stream, [IO.Compression.ZipArchiveMode]::Create, $true)
    try {
        foreach ($relativePath in $files) {
            if ($relativePath -eq 'start.cmd') {
                # Git may normalize newlines; the downloadable release always gets CRLF.
                $text = [IO.File]::ReadAllText((Join-Path $projectRoot $relativePath)) -replace '\r\n|\r|\n', "`r`n"
                $entryStream = $zip.CreateEntry('start.cmd').Open()
                try {
                    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($text)
                    $entryStream.Write($bytes, 0, $bytes.Length)
                } finally { $entryStream.Dispose() }
            } else {
                [IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip,
                    (Join-Path $projectRoot $relativePath), $relativePath) | Out-Null
            }
        }
    } finally { $zip.Dispose() }
} finally { $stream.Dispose() }
Write-Output "Release created without personal data: $destinationPath"
