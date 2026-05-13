# setup_task.ps1
# ---------------
# Registers a Windows Task Scheduler job that runs the Premier League
# data collector automatically at every user logon.
#
# Run once (no admin required for per-user tasks):
#   powershell -ExecutionPolicy Bypass -File sports/setup_task.ps1
#
# To remove the task later:
#   Unregister-ScheduledTask -TaskName "PremierLeagueCollector" -Confirm:$false

$TaskName    = "PremierLeagueCollector"
$SportsDir   = $PSScriptRoot
$ProjectRoot = Split-Path -Parent $SportsDir
$PythonExe   = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$ScriptPath  = Join-Path $SportsDir   "collector.py"

# Fall back to system python if the venv doesn't exist yet
if (-not (Test-Path $PythonExe)) {
    Write-Warning "Venv python not found at: $PythonExe"
    Write-Warning "Falling back to system 'python'. Run the venv setup first for best results."
    $PythonExe = "python"
}

Write-Host "Registering task '$TaskName' ..."
Write-Host "  Python : $PythonExe"
Write-Host "  Script : $ScriptPath"
Write-Host ""

$Action = New-ScheduledTaskAction `
    -Execute         $PythonExe `
    -Argument        "`"$ScriptPath`"" `
    -WorkingDirectory $ProjectRoot

# Fire at every logon; delay 30 s to let the network come up first
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Trigger.Delay = "PT30S"

$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit      (New-TimeSpan -Minutes 10) `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -MultipleInstances        IgnoreNew

# Remove any existing version so we can re-register cleanly
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed previous task registration."
}

Register-ScheduledTask `
    -TaskName    $TaskName `
    -Action      $Action   `
    -Trigger     $Trigger  `
    -Settings    $Settings `
    -Description "Collects Premier League data on Mon/Tue/Wed at logon (football-data.org)" `
    -RunLevel    Limited   | Out-Null

Write-Host ""
Write-Host "[OK] Task '$TaskName' registered successfully."
Write-Host ""
Write-Host "Useful commands:"
Write-Host "  Run now   : Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "  View log  : Get-Content '$SportsDir\logs\collector.log' -Tail 30"
Write-Host "  Remove    : Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
Write-Host ""
Write-Host "Don't forget to set your API key:"
Write-Host "  [System.Environment]::SetEnvironmentVariable('FOOTBALL_API_KEY','<your_key>','User')"
Write-Host "  Or paste your key into: $SportsDir\api_key.txt"
