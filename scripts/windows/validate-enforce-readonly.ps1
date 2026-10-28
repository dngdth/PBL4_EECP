param(
    [string]$ServerHost = "",
    [int]$GatewayPort = 8443,
    [int]$BackendPort = 8000
)

$ErrorActionPreference = "Stop"
$serviceName = "EECPAgentService"

Write-Output "== SCM identity/state =="
Get-CimInstance Win32_Service -Filter "Name='$serviceName'" |
    Select-Object Name, State, StartMode, StartName, PathName

Write-Output "== Named Pipe =="
Get-ChildItem -LiteralPath "\\.\pipe\" |
    Where-Object Name -EQ "eecp-agent-v2" |
    Select-Object Name

Write-Output "== Service and local-token ACLs =="
if (Test-Path -LiteralPath "$env:ProgramData\EECP") {
    & icacls.exe "$env:ProgramData\EECP"
}
if (Test-Path -LiteralPath "$env:ProgramData\EECP-Client\local-event.token") {
    & icacls.exe "$env:ProgramData\EECP-Client\local-event.token"
}

Write-Output "== Exact EECP Firewall identities =="
$rules = @(Get-NetFirewallRule -Name "EECP-*" -ErrorAction SilentlyContinue)
$rules | Select-Object Name, DisplayName, Group, Enabled, Direction, Action, Profile
$rules | Get-NetFirewallAddressFilter | Select-Object InstanceID, RemoteAddress

if ($ServerHost) {
    Write-Output "== LAN reachability =="
    Test-NetConnection $ServerHost -Port $GatewayPort |
        Select-Object ComputerName, RemotePort, TcpTestSucceeded
    Test-NetConnection $ServerHost -Port $BackendPort |
        Select-Object ComputerName, RemotePort, TcpTestSucceeded
    Write-Output "TLS health requests intentionally use the machine trust store."
    Invoke-RestMethod "https://${ServerHost}:${GatewayPort}/health"
    Write-Output "Backend TCP was checked; validate its localhost health on Machine A."
}
