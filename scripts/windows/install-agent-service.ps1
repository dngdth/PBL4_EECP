param(
    [ValidateSet("Install", "Remove", "Start", "Stop", "Restart", "Status")]
    [string]$Action = "Status",
    [string]$PythonExe = "python",
    [string]$EnvironmentFile = ""
)

$ErrorActionPreference = "Stop"
$serviceName = "EECPAgentService"
$serviceDataDirectory = Join-Path $env:ProgramData "EECP"
$installedEnvironmentFile = Join-Path $serviceDataDirectory "service.env"

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
        & $PythonExe -m agent.service.main --scm install
        if ($LASTEXITCODE -ne 0) { throw "Service installation failed" }
        sc.exe failure $serviceName reset= 86400 actions= restart/5000/restart/15000/restart/60000
        sc.exe failureflag $serviceName 1
        $service = Get-CimInstance Win32_Service -Filter "Name='$serviceName'"
        if ($null -eq $service -or $service.StartName -ne "LocalSystem") {
            throw "Service must be installed as LocalSystem"
        }
    }
    "Remove" { & $PythonExe -m agent.service.main --scm remove }
    "Start" { & $PythonExe -m agent.service.main --scm start }
    "Stop" { & $PythonExe -m agent.service.main --scm stop }
    "Restart" { & $PythonExe -m agent.service.main --scm restart }
    "Status" { Get-Service -Name $serviceName }
}
