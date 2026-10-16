# The documented Windows install (docs/install.md#windows), run as a standard user named in Japanese (profile
# C:\Users\<name>) with the console in code page 932, as on Japanese Windows. Each step's stdout and stderr are kept
# byte for byte in C:\qa\<step>.out / .err. ASCII only: Windows PowerShell reads a BOM-less script in the ANSI code page.
param([string]$Source = 'interlatch', [string]$Phase = 'install')
$ErrorActionPreference = 'Continue'
$qa = 'C:\qa'
$log = Join-Path $qa 'user.log'
function Log([string]$s) { Add-Content -LiteralPath $log -Value $s -Encoding UTF8 }
function Step([string]$name, [string]$exe, [string]$arguments) {
    $o = Join-Path $qa "$name.out"; $e = Join-Path $qa "$name.err"
    try {
        $p = Start-Process -FilePath $exe -ArgumentList $arguments -NoNewWindow -Wait -PassThru `
            -RedirectStandardOutput $o -RedirectStandardError $e
        Log ("=== {0}: {1} {2}  -> exit {3}" -f $name, $exe, $arguments, $p.ExitCode)
    } catch {
        Log ("=== {0}: {1} {2}  -> could not start: {3}" -f $name, $exe, $arguments, $_)
    }
}

chcp.com 932 | Out-Null
$env:Path = (Join-Path $env:USERPROFILE '.local\bin') + ';' + $env:Path
$py = "--python 3.13"

if ($Phase -eq 'install') {
    Log ("user={0} profile={1} console_cp={2} culture={3} ui={4}" -f $env:USERNAME, $env:USERPROFILE,
        [Console]::OutputEncoding.CodePage, (Get-Culture).Name, (Get-UICulture).Name)
    Step '01-uv-installer' 'powershell.exe' '-NoProfile -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
    Step '02-uv-tool-install' 'uv.exe' "tool install $py $Source"
    Step '03-version' 'interlatch.exe' '--version'
    Step '04-prepare' 'uv.exe' "run --no-project $py python C:\qa\ja_check.py prepare"
    Step '05-install' 'interlatch.exe' 'install'
    Step '06-status' 'interlatch.exe' 'status'
    Step '07-check' 'uv.exe' "run --no-project $py python C:\qa\ja_check.py check"
    # a dashboard for the screenshots (the Dashboard task needs an interactive sign-in, which CI has not got)
    Start-Process -FilePath 'interlatch.exe' -ArgumentList 'ui --port 11530' -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $qa 'ui.out') -RedirectStandardError (Join-Path $qa 'ui.err')
    Start-Sleep -Seconds 8
    Log 'dashboard started on :11530'
} else {
    Step '20-uninstall' 'interlatch.exe' 'uninstall'
    Step '21-tasks-after' 'schtasks.exe' '/Query /TN \Interlatch\Sync'
}
Log "done $Phase"
