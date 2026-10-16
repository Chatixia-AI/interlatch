# Runs user.ps1 as a new standard (non-admin) local user named in Japanese, through a scheduled task with its password
# (CI has no interactive sign-in to run it in), and waits for it. ASCII only, as user.ps1.
param([string]$Source = 'interlatch', [string]$Phase = 'install')
$ErrorActionPreference = 'Stop'
$name = -join ([char]0x5C71, [char]0x7530, [char]0x592A, [char]0x90CE)  # Yamada Taro
$qa = 'C:\qa'
$pwFile = Join-Path $env:RUNNER_TEMP 'qa-user-pw.txt'
if ($Phase -eq 'install') {
    New-Item -ItemType Directory -Force $qa | Out-Null
    $pw = 'Qa-' + [guid]::NewGuid().ToString('N').Substring(0, 16) + '!a1'
    Set-Content -LiteralPath $pwFile -Value $pw
    New-LocalUser -Name $name -Password (ConvertTo-SecureString $pw -AsPlainText -Force) -PasswordNeverExpires | Out-Null
    Add-LocalGroupMember -Group (Get-LocalGroup -SID 'S-1-5-32-545') -Member $name  # Users, not Administrators
    icacls.exe $qa /grant "${name}:(OI)(CI)F" /T | Out-Null
    # a task with a stored password signs in as a batch job, which a new standard user may not
    $sid = (New-Object System.Security.Principal.NTAccount($name)).Translate([System.Security.Principal.SecurityIdentifier]).Value
    $inf = Join-Path $env:RUNNER_TEMP 'rights.inf'
    secedit.exe /export /cfg $inf /areas USER_RIGHTS | Out-Null
    $lines = Get-Content -LiteralPath $inf
    if ($lines -match '^SeBatchLogonRight') {
        $lines = $lines -replace '^(SeBatchLogonRight = .*)$', "`$1,*$sid"
    } else {
        $lines = $lines -replace '^\[Privilege Rights\]$', "[Privilege Rights]`r`nSeBatchLogonRight = *$sid"
    }
    Set-Content -LiteralPath $inf -Value $lines -Encoding Unicode
    secedit.exe /configure /db (Join-Path $env:RUNNER_TEMP 'rights.sdb') /cfg $inf /areas USER_RIGHTS | Out-Null
    wevtutil.exe sl Microsoft-Windows-TaskScheduler/Operational /e:true
} else {
    $pw = Get-Content -LiteralPath $pwFile
}
$task = "qa-ja-$Phase"
$arg = "-NoProfile -ExecutionPolicy Bypass -File C:\qa\user.ps1 -Phase $Phase -Source `"$Source`""
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName $task -Action $action -Settings $settings -User $name -Password $pw -RunLevel Limited | Out-Null
Start-ScheduledTask -TaskName $task
$started = Get-Date
# 267011: has not run yet; 267009: running
do {
    Start-Sleep -Seconds 5
    $state = (Get-ScheduledTask -TaskName $task).State
    $info = Get-ScheduledTaskInfo -TaskName $task
    $waiting = $state -in 'Running', 'Queued' -or $info.LastTaskResult -in 267011, 267009
    $minutes = ((Get-Date) - $started).TotalMinutes
} while ($waiting -and $minutes -lt 30 -and -not ($info.LastTaskResult -eq 267011 -and $state -eq 'Ready' -and $minutes -gt 3))
Write-Host ("task {0}: state {1}, last result {2}, last run {3}" -f $task, $state, $info.LastTaskResult, $info.LastRunTime)
if ($info.LastTaskResult -eq 267011) {
    Get-WinEvent -LogName Microsoft-Windows-TaskScheduler/Operational -MaxEvents 30 -ErrorAction SilentlyContinue |
        Where-Object { $_.Message -match 'qa-ja' } | ForEach-Object { Write-Host ("{0} {1}: {2}" -f $_.TimeCreated, $_.Id, $_.Message) }
}
