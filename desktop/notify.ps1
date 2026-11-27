# 信匣桌面通知器 —— 托盘常驻，新邮件弹窗，验证码一键复制
# 只依赖 Windows 自带的 PowerShell 5 + .NET WinForms
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12
[System.Net.WebRequest]::DefaultWebProxy = New-Object System.Net.WebProxy
$env:NO_PROXY = '127.0.0.1,localhost'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$CfgPath = Join-Path $ScriptDir 'config.json'
$StateDir = Join-Path $env:APPDATA 'xinxia-notify'
if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Path $StateDir | Out-Null }
$SeenPath = Join-Path $StateDir 'seen.json'
Add-Content (Join-Path $StateDir 'debug.log') -Value ((Get-Date -Format 'HH:mm:ss') + ' start ' + $ScriptDir)

$Cfg = @{ BaseUrl = 'https://agvuph.cn'; PollSeconds = 5 }
if (Test-Path $CfgPath) {
    try {
        $raw = Get-Content $CfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($raw.BaseUrl) { $Cfg.BaseUrl = $raw.BaseUrl.TrimEnd('/') }
        if ($raw.PollSeconds) { $Cfg.PollSeconds = [int]$raw.PollSeconds }
    } catch {}
}
if ($Cfg.PollSeconds -lt 3) { $Cfg.PollSeconds = 3 }

$script:Seen = @{}
if (Test-Path $SeenPath) {
    try {
        (Get-Content $SeenPath -Raw -Encoding UTF8 | ConvertFrom-Json) | ForEach-Object { $script:Seen[[string]$_] = $true }
    } catch {}
}

function Save-Seen {
    try {
        $ids = @($script:Seen.Keys)
        if ($ids.Count -gt 300) { $ids = $ids[-300..-1] }
        ($ids | ConvertTo-Json -Compress) | Set-Content $SeenPath -Encoding UTF8
    } catch {}
}

$script:Session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
$script:LoggedIn = $false
$script:FirstRun = $true
$script:Backoff = 0
$script:NextTry = [DateTime]::MinValue
$script:Queue = New-Object System.Collections.Queue
$script:Popup = $null
$script:Paused = $false
$script:CopyBtn = $null
$script:CopyTimer = New-Object System.Windows.Forms.Timer
$script:CopyTimer.Interval = 1200
$script:CopyTimer.Add_Tick({
    $script:CopyTimer.Stop()
    $b = $script:CopyBtn
    if ($b -and -not $b.IsDisposed) {
        $b.Text = '复制验证码'
        $b.BackColor = [System.Drawing.Color]::FromArgb(42, 87, 76)
        $b.ForeColor = [System.Drawing.Color]::White
        $b.Enabled = $true
    }
})

function Fetch-Updates {
    if ($script:Paused) { return }
    if ([DateTime]::Now -lt $script:NextTry) { return }
    try {
        $res = Invoke-RestMethod -Uri "$($Cfg.BaseUrl)/api/updates" -TimeoutSec 12
        $list = @($res.messages)
        if ($script:FirstRun) {
            $list | ForEach-Object { $script:Seen[[string]$_.id] = $true }
            $script:FirstRun = $false
            Save-Seen
        } else {
            $fresh = @()
            foreach ($m in $list) {
                $k = [string]$m.id
                if (-not $script:Seen.ContainsKey($k)) {
                    $script:Seen[$k] = $true
                    $fresh += $m
                }
            }
            if ($fresh.Count -gt 0) {
                Add-Content (Join-Path $StateDir 'debug.log') -Value ((Get-Date -Format 'HH:mm:ss') + ' fresh ' + $fresh.Count)
                Save-Seen
                # 一条条排队，避免一次弹一堆
                [array]::Reverse($fresh)
                foreach ($m in $fresh) { $script:Queue.Enqueue($m) }
                Show-Next
            }
        }
        $script:Backoff = 0
        $script:NextTry = [DateTime]::MinValue
        Update-TrayText '信匣 · 自动收信中'
    } catch {
        try { Add-Content (Join-Path $StateDir 'debug.log') -Value ((Get-Date -Format 'HH:mm:ss') + ' ' + $_.Exception.Message) } catch {}
        $script:Backoff = [Math]::Min([Math]::Max(1, $script:Backoff + 1), 6)
        $wait = 10 * [Math]::Pow(2, $script:Backoff - 1)
        if ($wait -gt 300) { $wait = 300 }
        $script:NextTry = [DateTime]::Now.AddSeconds($wait)
        Update-TrayText ("信匣 · 连接异常，{0} 秒后重试" -f [int]$wait)
    }
}

function Update-TrayText([string]$text) {
    if ($script:Tray) { $script:Tray.Text = $text.Substring(0, [Math]::Min(63, $text.Length)) }
}

function Show-Next {
    if ($script:Popup -and -not $script:Popup.IsDisposed) { return }
    if ($script:Queue.Count -eq 0) { return }
    $m = $script:Queue.Dequeue()

    $f = New-Object System.Windows.Forms.Form
    $f.FormBorderStyle = 'None'
    $f.StartPosition = 'Manual'
    $f.TopMost = $true
    $f.ShowInTaskbar = $false
    $f.BackColor = [System.Drawing.Color]::White
    $f.Width = 380
    $f.Height = 190
    $wa = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
    $f.Left = $wa.Right - $f.Width - 16
    $f.Top = $wa.Bottom - $f.Height - 16

    $border = New-Object System.Windows.Forms.Panel
    $border.Dock = 'Fill'
    $border.Padding = New-Object System.Windows.Forms.Padding(1)
    $border.BackColor = [System.Drawing.Color]::FromArgb(214, 222, 218)
    $f.Controls.Add($border)

    $inner = New-Object System.Windows.Forms.Panel
    $inner.Dock = 'Fill'
    $inner.BackColor = [System.Drawing.Color]::White
    $inner.Padding = New-Object System.Windows.Forms.Padding(16, 12, 16, 12)
    $border.Controls.Add($inner)

    $to = New-Object System.Windows.Forms.Label
    $to.Text = [string]$m.address
    $to.Font = New-Object System.Drawing.Font('Segoe UI', 9)
    $to.ForeColor = [System.Drawing.Color]::FromArgb(106, 116, 111)
    $to.AutoSize = $false
    $to.Dock = 'Top'
    $to.Height = 18
    $inner.Controls.Add($to)

    $subj = New-Object System.Windows.Forms.Label
    $subj.Text = [string]$m.subject
    $subj.Font = New-Object System.Drawing.Font('Segoe UI', 10, [System.Drawing.FontStyle]::Bold)
    $subj.ForeColor = [System.Drawing.Color]::FromArgb(32, 38, 35)
    $subj.AutoSize = $false
    $subj.Dock = 'Top'
    $subj.Height = 40
    $inner.Controls.Add($subj)
    $subj.BringToFront()

    $code = New-Object System.Windows.Forms.Label
    $code.Text = [string]$m.code
    $code.Font = New-Object System.Drawing.Font('Consolas', 22, [System.Drawing.FontStyle]::Bold)
    $code.ForeColor = [System.Drawing.Color]::FromArgb(42, 87, 76)
    $code.AutoSize = $false
    $code.Dock = 'Top'
    $code.Height = 38
    $inner.Controls.Add($code)
    $code.BringToFront()

    $bar = New-Object System.Windows.Forms.Panel
    $bar.Dock = 'Bottom'
    $bar.Height = 30
    $inner.Controls.Add($bar)

    $copy = New-Object System.Windows.Forms.Button
    $copy.Text = '复制验证码'
    $copy.Width = 96
    $copy.Height = 28
    $copy.Left = 0
    $copy.Top = 0
    $copy.FlatStyle = 'Flat'
    $copy.BackColor = [System.Drawing.Color]::FromArgb(42, 87, 76)
    $copy.ForeColor = [System.Drawing.Color]::White
    $copy.FlatAppearance.BorderSize = 0
    $copy.Add_Click({
        $want = [string]$m.code
        $ok = $false
        try {
            # 先清空再写入，写完立刻读回来比对，不靠“调用过就成功”
            [System.Windows.Forms.Clipboard]::Clear()
            [System.Windows.Forms.Clipboard]::SetText($want)
            $got = [System.Windows.Forms.Clipboard]::GetText()
            $ok = ($got -eq $want)
        } catch { $ok = $false }
        if ($ok) {
            $copy.Text = '已复制'
            $copy.BackColor = [System.Drawing.Color]::FromArgb(42, 87, 76)
        } else {
            $copy.Text = '复制失败'
            $copy.BackColor = [System.Drawing.Color]::FromArgb(162, 75, 51)
        }
        $copy.ForeColor = [System.Drawing.Color]::White
        $copy.Enabled = $false
        $script:CopyBtn = $copy
        $script:CopyTimer.Stop()
        $script:CopyTimer.Start()
    })
    $copy.FlatAppearance.MouseOverBackColor = [System.Drawing.Color]::FromArgb(31, 67, 58)
    $copy.FlatAppearance.MouseDownBackColor = [System.Drawing.Color]::FromArgb(24, 57, 47)
    $bar.Controls.Add($copy)

    $open = New-Object System.Windows.Forms.Button
    $open.Text = '打开网站'
    $open.Width = 80
    $open.Height = 28
    $open.Left = 104
    $open.Top = 0
    $open.FlatStyle = 'Flat'
    $open.BackColor = [System.Drawing.Color]::White
    $open.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(214, 222, 218)
    $open.Add_Click({ Start-Process ($Cfg.BaseUrl + '/#me') })
    $open.FlatAppearance.MouseOverBackColor = [System.Drawing.Color]::FromArgb(250, 248, 243)
    $open.FlatAppearance.MouseDownBackColor = [System.Drawing.Color]::FromArgb(241, 237, 228)
    $bar.Controls.Add($open)

    $close = New-Object System.Windows.Forms.Button
    $close.Text = '关闭'
    $close.Width = 56
    $close.Height = 28
    $close.Left = 190
    $close.Top = 0
    $close.FlatStyle = 'Flat'
    $close.BackColor = [System.Drawing.Color]::White
    $close.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(214, 222, 218)
    $close.Add_Click({ $f.Close() })
    $close.FlatAppearance.MouseOverBackColor = [System.Drawing.Color]::FromArgb(250, 248, 243)
    $close.FlatAppearance.MouseDownBackColor = [System.Drawing.Color]::FromArgb(241, 237, 228)
    $bar.Controls.Add($close)

    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 25000
    $timer.Add_Tick({ $timer.Stop(); if (-not $f.IsDisposed) { $f.Close() } })
    $timer.Start()

    $f.Add_FormClosed({ $script:Popup = $null; $timer.Dispose(); Show-Next })
    $script:Popup = $f
    $f.Show()
    Add-Content (Join-Path $StateDir 'debug.log') -Value ((Get-Date -Format 'HH:mm:ss') + ' popup ' + [string]$m.code)
    [System.Media.SystemSounds]::Asterisk.Play()
}

# ---- 托盘 ----
$script:MainForm = New-Object System.Windows.Forms.Form
$script:MainForm.WindowState = 'Minimized'
$script:MainForm.ShowInTaskbar = $false
$script:MainForm.Visible = $false
$script:MainForm.Add_Shown({ $script:MainForm.Hide() })

$script:Tray = New-Object System.Windows.Forms.NotifyIcon
$script:Tray.Icon = [System.Drawing.SystemIcons]::Application
$script:Tray.Visible = $true
$script:Tray.Text = '信匣 · 启动中'

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$miCheck = $menu.Items.Add('立即检查')
$miCheck.Add_Click({ $script:NextTry = [DateTime]::MinValue; Fetch-Updates })
$miPause = $menu.Items.Add('暂停 / 继续')
$miPause.Add_Click({
    $script:Paused = -not $script:Paused
    Update-TrayText ($(if ($script:Paused) { '信匣 · 已暂停' } else { '信匣 · 自动收信中' }))
})
$miOpen = $menu.Items.Add('打开网站')
$miOpen.Add_Click({ Start-Process ($Cfg.BaseUrl + '/#me') })
[void]$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator))
$miExit = $menu.Items.Add('退出')
$miExit.Add_Click({ $script:Tray.Visible = $false; $script:MainForm.Close() })
$script:Tray.ContextMenuStrip = $menu
$script:Tray.Add_DoubleClick({ Start-Process ($Cfg.BaseUrl + '/#me') })

$poll = New-Object System.Windows.Forms.Timer
$poll.Interval = $Cfg.PollSeconds * 1000
$poll.Add_Tick({ Fetch-Updates })
$poll.Start()

Fetch-Updates
[System.Windows.Forms.Application]::Run($script:MainForm)
