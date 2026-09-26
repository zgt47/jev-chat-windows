$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoZip = "https://github.com/zgt47/jev-chat-windows/archive/refs/heads/dev-external-source.zip"
$Temp = Join-Path $env:TEMP ("jev-chat-dev-update-" + [Guid]::NewGuid().ToString("N"))
$Zip = Join-Path $Temp "src.zip"
$Extract = Join-Path $Temp "src"

function Write-Step([string]$Text) {
    Write-Host ("[进行中] " + $Text) -ForegroundColor Cyan
}

try {
    Write-Host "只更新 main.py / app / core，不碰 _internal、config.json、chat_profiles.json。" -ForegroundColor Gray
    Write-Host ""

    New-Item -ItemType Directory -Force -Path $Temp | Out-Null

    Write-Step "下载 dev-external-source 最新源码"
    try {
        Invoke-WebRequest -Uri $RepoZip -OutFile $Zip -UseBasicParsing -TimeoutSec 60
    }
    catch {
        throw "下载源码失败。请检查 GitHub 是否能正常访问。原始错误：$($_.Exception.Message)"
    }

    if (-not (Test-Path $Zip) -or (Get-Item $Zip).Length -lt 1024) {
        throw "下载到的源码压缩包无效或为空"
    }

    Write-Step "解压源码"
    Expand-Archive -Path $Zip -DestinationPath $Extract -Force
    $Source = Get-ChildItem $Extract -Directory | Select-Object -First 1
    if (-not $Source) {
        throw "下载包结构异常：没有找到源码目录"
    }

    foreach ($name in @("app", "core")) {
        $src = Join-Path $Source.FullName $name
        $dst = Join-Path $Root $name
        if (-not (Test-Path $src)) {
            throw "下载包缺少 $name 目录"
        }

        $backup = Join-Path $Temp ($name + "-old")
        if (Test-Path $dst) {
            Move-Item $dst $backup -Force
        }

        try {
            Copy-Item $src $dst -Recurse -Force
        }
        catch {
            if (Test-Path $dst) {
                Remove-Item $dst -Recurse -Force -ErrorAction SilentlyContinue
            }
            if (Test-Path $backup) {
                Move-Item $backup $dst -Force
            }
            throw
        }
    }

    $mainSrc = Join-Path $Source.FullName "main.py"
    if (-not (Test-Path $mainSrc)) {
        throw "下载包缺少 main.py"
    }
    Copy-Item $mainSrc (Join-Path $Root "main.py") -Force

    $guideSrc = Join-Path $Source.FullName "DEV使用说明.md"
    if (Test-Path $guideSrc) {
        Copy-Item $guideSrc (Join-Path $Root "DEV使用说明.md") -Force
    }

    Write-Host ""
    Write-Host "[成功] 开发源码更新完成。" -ForegroundColor Green
    exit 0
}
catch {
    Write-Host ""
    Write-Host ("[失败] " + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
finally {
    if (Test-Path $Temp) {
        Remove-Item $Temp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
