[CmdletBinding()]
param(
    [ValidateSet('Install','Remove')][string]$Mode = 'Install',
    [string]$GamePath = ''
)
$ErrorActionPreference = 'Stop'

function Get-PatchHash([string]$Path) {
    $stream = [System.IO.File]::OpenRead($Path)
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try { return [System.BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $hasher.Dispose(); $stream.Dispose() }
}

try {
    $info = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'build-info.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $pakName = 'pakchunk99-Russian_P.pak'
    $relativePak = Join-Path 'Tale\Content\Paks' $pakName
    $packagePak = Join-Path $PSScriptRoot $relativePak
    if ($Mode -eq 'Install') {
        if (!(Test-Path -LiteralPath $packagePak -PathType Leaf)) { throw 'В архиве нет файла русификатора. Сначала распакуйте весь ZIP.' }
        if ((Get-PatchHash $packagePak) -ne $info.patch_sha256) {
            throw 'Контрольная сумма русификатора не совпадает. Заново скачайте и распакуйте архив.'
        }
    }
    if (!$GamePath) {
        $steamRoots = @()
        $steamRegistry = Get-ItemProperty -LiteralPath 'HKCU:\Software\Valve\Steam' -ErrorAction SilentlyContinue
        if ($steamRegistry.SteamPath) { $steamRoots += $steamRegistry.SteamPath }
        if (${env:ProgramFiles(x86)}) { $steamRoots += (Join-Path ${env:ProgramFiles(x86)} 'Steam') }
        $libraryRoots = @($steamRoots)
        foreach ($steamRoot in ($steamRoots | Select-Object -Unique)) {
            $vdf = Join-Path $steamRoot 'steamapps\libraryfolders.vdf'
            if (Test-Path -LiteralPath $vdf -PathType Leaf) {
                $vdfText = Get-Content -LiteralPath $vdf -Raw
                foreach ($match in [regex]::Matches($vdfText, '"path"\s+"([^"]+)"')) {
                    $libraryRoots += $match.Groups[1].Value.Replace('\\', '\')
                }
            }
        }
        $foundGames = @($libraryRoots | Select-Object -Unique | ForEach-Object {
            $candidate = Join-Path $_ 'steamapps\common\HELLCARD II Playtest'
            if (Test-Path -LiteralPath (Join-Path $candidate 'Tale\Binaries\Win64\TaleDemo.exe') -PathType Leaf) { $candidate }
        } | Select-Object -Unique)
        if ($foundGames.Count -eq 1) { $GamePath = $foundGames[0] }
        else { $GamePath = Read-Host 'Введите полный путь к папке HELLCARD II Playtest' }
    }
    $GamePath = (Resolve-Path -LiteralPath $GamePath).Path
    if (!(Test-Path -LiteralPath (Join-Path $GamePath 'Tale\Binaries\Win64\TaleDemo.exe') -PathType Leaf)) {
        throw 'Это не папка установленной HELLCARD II Playtest.'
    }
    if (Get-Process -Name TaleDemo,TalePlaytestSteam -ErrorAction SilentlyContinue) {
        throw 'Сначала закройте HELLCARD II Playtest и повторите запуск установщика.'
    }
    $pakDirectory = (Resolve-Path -LiteralPath (Join-Path $GamePath 'Tale\Content\Paks')).Path
    $destination = Join-Path $pakDirectory $pakName
    if ($Mode -eq 'Remove') {
        if (Test-Path -LiteralPath $destination -PathType Leaf) {
            if ((Get-PatchHash $destination) -ne $info.patch_sha256) {
                throw 'Установлен другой вариант патча. Этот удалитель его не изменит. Для ручного удаления: Tale\Content\Paks\pakchunk99-Russian_P.pak'
            }
            Remove-Item -LiteralPath $destination
            Write-Host 'Русификатор удалён. Запустите игру заново.'
        } else { Write-Host 'Этот русификатор уже отсутствует.' }
        exit 0
    }
    $steamAppsDirectory = Split-Path -Parent (Split-Path -Parent $GamePath)
    $manifestPath = Join-Path $steamAppsDirectory 'appmanifest_5054420.acf'
    if (Test-Path -LiteralPath $manifestPath -PathType Leaf) {
        $manifestText = Get-Content -LiteralPath $manifestPath -Raw
        $buildMatch = [regex]::Match($manifestText, '"buildid"\s+"(\d+)"')
        if ($buildMatch.Success -and $buildMatch.Groups[1].Value -ne $info.steam_build_id) {
            throw ('У вас сборка Steam ' + $buildMatch.Groups[1].Value + ', а этот патч рассчитан на ' + $info.steam_build_id + '. Нужна обновлённая версия перевода.')
        }
    }
    if (Test-Path -LiteralPath $destination -PathType Leaf) {
        if ((Get-PatchHash $destination) -eq $info.patch_sha256) {
            Write-Host 'Этот русификатор уже установлен.'
            exit 0
        }
        $backupDirectory = Join-Path $GamePath ('Tale\Saved\Backups\HellcardRussian\' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
        New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
        Copy-Item -LiteralPath $destination -Destination (Join-Path $backupDirectory $pakName)
        Write-Host ('Предыдущий вариант патча сохранён: ' + $backupDirectory)
    }
    $pending = Join-Path $pakDirectory ($pakName + '.pending')
    Copy-Item -LiteralPath $packagePak -Destination $pending -Force
    if ((Get-PatchHash $pending) -ne $info.patch_sha256) {
        throw 'Ошибка проверки скопированного файла.'
    }
    Move-Item -LiteralPath $pending -Destination $destination -Force
    Write-Host 'Русификатор установлен. Запустите игру и выберите русский язык в настройках, если он не включился автоматически.'
    Write-Host ('Версия перевода: ' + $info.mod_version + '; сборка Steam: ' + $info.steam_build_id)
} catch {
    Write-Host ('Ошибка: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
