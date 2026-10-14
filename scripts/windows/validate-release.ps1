param(
    [switch]$ConfirmIsolatedTestMachine,
    [switch]$IncludeUsb,
    [string]$PythonExe = "python"
)

$ErrorActionPreference = "Stop"
$serviceName = "EECPAgentService"
$testRule = "EECP-TEST-PHASE9-VALIDATION"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$testAddress = "203.0.113.254"
$ruleCreated = $false

function Assert-Preflight {
    if ($env:OS -ne "Windows_NT") {
        throw "Windows target validation requires Windows."
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run from an elevated Administrator PowerShell on the isolated VM."
    }
    if (-not $ConfirmIsolatedTestMachine) {
        throw "Refusing OS mutation without -ConfirmIsolatedTestMachine."
    }
    foreach ($path in @(
        "agent\service\main.py",
        "scripts\windows\install-agent-service.ps1",
        "apps\api\test\unit\test_phase9_ipc_security.py"
    )) {
        if (-not (Test-Path -LiteralPath (Join-Path $repoRoot $path))) {
            throw "Required release file is missing: $path"
        }
    }
}

Assert-Preflight
Push-Location $repoRoot
try {
    Write-Host "[1/7] Security and Windows transport regression"
    & uv run pytest `
        apps/api/test/unit/test_phase9_ipc_security.py `
        apps/api/test/unit/test_named_pipe_transport_windows.py `
        apps/api/test/unit/test_phase6_firewall.py -q
    if ($LASTEXITCODE -ne 0) { throw "Security/transport regression failed" }

    Write-Host "[2/7] SCM and LocalSystem preflight"
    $service = Get-CimInstance Win32_Service -Filter "Name='$serviceName'" -ErrorAction SilentlyContinue
    if ($null -eq $service) {
        throw "Install the Agent Service first with scripts/windows/install-agent-service.ps1 -Action Install"
    }
    if ($service.StartName -ne "LocalSystem") {
        throw "Agent Service account is '$($service.StartName)', expected LocalSystem"
    }
    if ($service.State -ne "Running") {
        & "$repoRoot\scripts\windows\install-agent-service.ps1" -Action Start -PythonExe $PythonExe
    }

    Write-Host "[3/7] Named Pipe availability"
    $pipe = Get-ChildItem -LiteralPath "\\.\pipe\" | Where-Object Name -eq "eecp-agent-v2"
    if ($null -eq $pipe) { throw "EECP Named Pipe was not exposed by the running Service" }

    Write-Host "[4/7] Isolated Windows Firewall create/query/delete check"
    & netsh advfirewall firewall add rule name=$testRule dir=out action=block remoteip=$testAddress profile=any
    if ($LASTEXITCODE -ne 0) { throw "Failed to create isolated EECP test rule" }
    $ruleCreated = $true
    $shown = & netsh advfirewall firewall show rule name=$testRule
    if ($LASTEXITCODE -ne 0 -or ($shown -join "`n") -notmatch [regex]::Escape($testAddress)) {
        throw "EECP test Firewall rule could not be verified"
    }

    Write-Host "[5/7] Command authority checks"
    Write-Host "Forged RESTORE, tampered command, expiry, Session binding, persistent replay, and signed Policy: PASS in regression."

    Write-Host "[6/7] Manual normal-user lifecycle gate"
    Write-Host "From a non-elevated login: start Agent Client, authorize APPLY, verify hosts/process/Firewall; kill Client; wait for Service maintenance; restart Client; finish Session and verify RESTORE preserves unrelated rules."
    if ($IncludeUsb) {
        Write-Warning "USB validation was explicitly requested. Use sacrificial removable media and verify baseline restoration; this script does not globally disable USB."
    } else {
        Write-Host "USB hardware validation: SKIPPED (use -IncludeUsb on a sacrificial VM)."
    }

    Write-Host "[7/7] SCM recovery gate"
    $recovery = & sc.exe qfailure $serviceName
    if ($LASTEXITCODE -ne 0 -or ($recovery -join "`n") -notmatch "RESTART") {
        throw "SCM recovery actions are not configured"
    }
    Write-Host "Automated preflight PASS. Complete the displayed normal-user lifecycle before declaring Windows validation PASS."
}
finally {
    if ($ruleCreated) {
        & netsh advfirewall firewall delete rule name=$testRule | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Could not remove exact test rule '$testRule'; remove it manually."
        }
    }
    Pop-Location
}
