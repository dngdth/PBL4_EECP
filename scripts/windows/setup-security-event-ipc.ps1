param(
    [ValidateSet("Install", "Remove", "Status")]
    [string]$Action = "Status",
    [string]$AgentUser = $env:USERNAME
)

$ErrorActionPreference = "Stop"
$directory = Join-Path $env:ProgramData "EECP-Client"
$tokenPath = Join-Path $directory "local-event.token"

switch ($Action) {
    "Install" {
        $identity = New-Object Security.Principal.NTAccount($env:USERDOMAIN, $AgentUser)
        $sid = $identity.Translate([Security.Principal.SecurityIdentifier]).Value
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
        $bytes = New-Object byte[] 32
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
        [IO.File]::WriteAllText(
            $tokenPath,
            [Convert]::ToBase64String($bytes),
            [Text.UTF8Encoding]::new($false)
        )
        & icacls.exe $directory /inheritance:r /grant:r `
            "*S-1-5-18:(OI)(CI)F" `
            "*S-1-5-32-544:(OI)(CI)F" `
            "*${sid}:(OI)(CI)RX"
        if ($LASTEXITCODE -ne 0) { throw "Could not protect local event IPC secret" }
        Write-Output "Security event IPC secret installed for $AgentUser ($sid)."
        Write-Output "Restart the EECP Agent Service and Agent Client."
    }
    "Remove" {
        if (Test-Path -LiteralPath $tokenPath) {
            Remove-Item -LiteralPath $tokenPath -Force
        }
        Write-Output "Security event IPC secret removed."
    }
    "Status" {
        Write-Output "Token exists: $(Test-Path -LiteralPath $tokenPath)"
        if (Test-Path -LiteralPath $tokenPath) {
            & icacls.exe $tokenPath
        }
    }
}
