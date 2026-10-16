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
    icacls.exe $qa /grant "${name}:(OI)(CI)F" | Out-Null
} else {
    $pw = Get-Content -LiteralPath $pwFile
}
$task = "qa-ja-$Phase"
$arg = "-NoProfile -ExecutionPolicy Bypass -File C:\qa\user.ps1 -Phase $Phase -Source `"$Source`""
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName $task -Action $action -Settings $settings -User $name -Password $pw -RunLevel Limited | Out-Null
Start-ScheduledTask -TaskName $task
$deadline = (Get-Date).AddMinutes(30)
do {
    Start-Sleep -Seconds 5
    $state = (Get-ScheduledTask -TaskName $task).State
} while ($state -eq 'Running' -and (Get-Date) -lt $deadline)
$info = Get-ScheduledTaskInfo -TaskName $task
Write-Host ("task {0}: state {1}, last result {2}" -f $task, $state, $info.LastTaskResult)
