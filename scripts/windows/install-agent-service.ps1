param(
    [ValidateSet("Install", "Remove", "Start", "Stop", "Status")]
    [string]$Action = "Status",
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"
$serviceName = "EECPAgentService"

switch ($Action) {
    "Install" {
        & $PythonExe -m agent.service.main --scm install
        if ($LASTEXITCODE -ne 0) { throw "Service installation failed" }
        sc.exe failure $serviceName reset= 86400 actions= restart/5000/restart/15000/restart/60000
        sc.exe failureflag $serviceName 1
    }
    "Remove" { & $PythonExe -m agent.service.main --scm remove }
    "Start" { & $PythonExe -m agent.service.main --scm start }
    "Stop" { & $PythonExe -m agent.service.main --scm stop }
    "Status" { Get-Service -Name $serviceName }
}
