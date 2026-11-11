param(
    [ValidateSet("Install", "Remove", "Start", "Stop", "Restart", "Status")]
    [string]$Action = "Status",
    [string]$PythonExe = "python",
    [string]$EnvironmentFile = "",
    [string]$CaCertPath = "",
    [switch]$ImportCaToRootStore,
    [switch]$SkipClientLogonTask
)

$ErrorActionPreference = "Stop"
$serviceName = "EECPAgentService"
$clientTaskName = "EECPAgentClient"
$serviceDataDirectory = Join-Path $env:ProgramData "EECP"
$installedEnvironmentFile = Join-Path $serviceDataDirectory "service.env"
$certsDirectory = Join-Path $serviceDataDirectory "certs"
$installedCaFile = Join-Path $certsDirectory "ca.crt"

switch ($Action) {
    "Install" {
        & $PythonExe -c "import servicemanager, win32event, win32service, win32serviceutil"
        if ($LASTEXITCODE -ne 0) { throw "pywin32 is not available to $PythonExe" }
        if ($EnvironmentFile) {
            $source = (Resolve-Path -LiteralPath $EnvironmentFile).Path
            New-Item -ItemType Directory -Force -Path $serviceDataDirectory | Out-Null
            Copy-Item -LiteralPath $source -Destination $installedEnvironmentFile -Force
            & icacls.exe $serviceDataDirectory /inheritance:r /grant:r `
                "*S-1-5-18:(OI)(CI)F" `
                "*S-1-5-32-544:(OI)(CI)F"
            if ($LASTEXITCODE -ne 0) { throw "Could not protect Service configuration" }
        }
        if ($CaCertPath) {
            $caSource = (Resolve-Path -LiteralPath $CaCertPath).Path
            New-Item -ItemType Directory -Force -Path $certsDirectory | Out-Null
            Copy-Item -LiteralPath $caSource -Destination $installedCaFile -Force
            & icacls.exe $certsDirectory /inheritance:r /grant:r `
                "*S-1-5-18:(OI)(CI)F" `
                "*S-1-5-32-544:(OI)(CI)F" `
                "*S-1-5-32-545:(OI)(CI)RX"
            if ($LASTEXITCODE -ne 0) { throw "Could not protect CA certificate directory" }
            if ($ImportCaToRootStore) {
                Import-Certificate -FilePath $installedCaFile -CertStoreLocation "Cert:\LocalMachine\Root" | Out-Null
            }
        }
        & $PythonExe -m agent.service.main --scm install
        if ($LASTEXITCODE -ne 0) { throw "Service installation failed" }
        sc.exe failure $serviceName reset= 86400 actions= restart/5000/restart/15000/restart/60000
        sc.exe failureflag $serviceName 1
        $service = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
        if ($null -eq $service -or $service.StartName -ne "LocalSystem") {
            throw "Service must be installed as LocalSystem"
        }
        if (-not $SkipClientLogonTask) {
            try {
                $resolvedPython = (Get-Command $PythonExe -ErrorAction SilentlyContinue).Source
                if (-not $resolvedPython) { $resolvedPython = $PythonExe }
                $projectRoot = (Resolve-Path ".").Path
                $action = New-ScheduledTaskAction -Execute $resolvedPython -Argument "-m agent.client.main" -WorkingDirectory $projectRoot
                $trigger = New-ScheduledTaskTrigger -AtLogOn
                $principal = New-ScheduledTaskPrincipal -GroupId "BUILTIN\Users" -RunLevel Limited
                $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 365)
                Register-ScheduledTask -TaskName $clientTaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
                Write-Host "Registered interactive client scheduled task: $clientTaskName"
            } catch {
                Write-Warning "Could not register scheduled task via cmdlets; falling back to schtasks.exe: $_"
                $cmd = "`"$PythonExe`" -m agent.client.main"
                & schtasks.exe /Create /TN $clientTaskName /TR $cmd /SC ONLOGON /F | Out-Null
            }
        }
    }
    "Remove" {
        try {
            Unregister-ScheduledTask -TaskName $clientTaskName -Confirm:$false -ErrorAction SilentlyContinue
        } catch {
            & schtasks.exe /Delete /TN $clientTaskName /F 2>$null
        }
        & $PythonExe -m agent.service.main --scm remove
    }
    "Start" { & $PythonExe -m agent.service.main --scm start }
    "Stop" { & $PythonExe -m agent.service.main --scm stop }
    "Restart" { & $PythonExe -m agent.service.main --scm restart }
    "Status" {
        Get-Service -Name $serviceName -ErrorAction SilentlyContinue
        Get-ScheduledTask -TaskName $clientTaskName -ErrorAction SilentlyContinue
    }
}
