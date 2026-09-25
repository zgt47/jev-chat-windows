$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoZip = "https://github.com/zgt47/jev-chat-windows/archive/refs/heads/dev-external-source.zip"
$Temp = Join-Path $env:TEMP ("jev-chat-dev-update-" + [Guid]::NewGuid().ToString("N"))
$Zip = Join-Path $Temp "src.zip"
$Extract = Join-Path $Temp "src"

Write-Host ""
Write-Host "JevChat-Windows 开发版源码更新" -ForegroundColor Cyan
Write-Host "只更新 main.py / app / core，不碰 runtime、config.json、chat_profiles.json。"
Write-Host ""

try {
    New-Item -ItemType Directory -Force -Path $Temp | Out-Null
    Write-Host "正在下载 dev-external-source 最新源码..."
    Invoke-WebRequest -Uri $RepoZip -OutFile $Zip -UseBasicParsing

    Expand-Archive -Path $Zip -DestinationPath $Extract -Force
    $Source = Get-ChildItem $Extract -Directory | Select-Object -First 1
    if (-not $Source) { throw "下载包结构异常" }

    foreach ($name in @("app", "core")) {
        $dst = Join-Path $Root $name
        if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
        Copy-Item (Join-Path $Source.FullName $name) $dst -Recurse -Force
    }

    Copy-Item (Join-Path $Source.FullName "main.py") (Join-Path $Root "main.py") -Force

    if (Test-Path (Join-Path $Source.FullName "DEV使用说明.md")) {
        Copy-Item (Join-Path $Source.FullName "DEV使用说明.md") (Join-Path $Root "DEV使用说明.md") -Force
    }

    Write-Host ""
    Write-Host "源码更新完成。" -ForegroundColor Green
    Write-Host "现在重新双击 JevChat-Dev.exe 即可测试。"
}
catch {
    Write-Host ""
    Write-Host ("更新失败：" + $_.Exception.Message) -ForegroundColor Red
}
finally {
    if (Test-Path $Temp) { Remove-Item $Temp -Recurse -Force -ErrorAction SilentlyContinue }
}

Write-Host ""
Read-Host "按回车关闭"
