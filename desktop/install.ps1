# 一键安装：写配置、开机自启、立即启动
param(
    [string]$Url = "https://agvuph.cn"
)
$ErrorActionPreference = 'Stop'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$cfg = Join-Path $dir 'config.json'
$example = Join-Path $dir 'config.example.json'

if (-not (Test-Path $cfg)) { Copy-Item $example $cfg }

$obj = Get-Content $cfg -Raw -Encoding UTF8 | ConvertFrom-Json
if ($Url) { $obj.BaseUrl = $Url.TrimEnd('/') }
if ($null -ne $obj.PSObject.Properties['AdminPass']) { $obj.PSObject.Properties.Remove('AdminPass') }
$obj | ConvertTo-Json | Set-Content $cfg -Encoding UTF8

# 开机自启：启动文件夹放一个指向 notify.vbs 的快捷方式
$vbs = Join-Path $dir 'notify.vbs'
$startup = [Environment]::GetFolderPath('Startup')
$lnk = Join-Path $startup '信匣通知器.lnk'
$sh = New-Object -ComObject WScript.Shell
$sc = $sh.CreateShortcut($lnk)
$sc.TargetPath = "$env:SystemRoot\System32\wscript.exe"
$sc.Arguments = '"' + $vbs + '"'
$sc.WorkingDirectory = $dir
$sc.IconLocation = "$env:SystemRoot\System32\shell32.dll,43"
$sc.Description = '信匣临时邮箱桌面通知'
$sc.Save()

# 立即启动一份（已在运行会再开一个，脚本里没有单实例锁，重装前先在托盘退出）
Start-Process "$env:SystemRoot\System32\wscript.exe" -ArgumentList ('"' + $vbs + '"')

Write-Host "已安装。启动项：$lnk" -ForegroundColor Green
Write-Host "服务器：$($obj.BaseUrl)　轮询：$($obj.PollSeconds) 秒"
Write-Host "托盘里能看到图标，右键可暂停 / 立即检查 / 退出。"
