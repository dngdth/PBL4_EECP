param(
    [string]$OutputDir = "local-secrets",
    [string[]]$ServerIPs = @(),
    [string[]]$ServerHosts = @(),
    [int]$Days = 365,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

$openssl = Get-Command openssl -ErrorAction SilentlyContinue
if (-not $openssl) {
    throw "OpenSSL was not found in PATH. Install OpenSSL or run from Git Bash / MSYS2."
}

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$resolvedDir = (Resolve-Path -LiteralPath $OutputDir).Path

$caKey = Join-Path $resolvedDir "ca.key"
$caCrt = Join-Path $resolvedDir "ca.crt"
$gwKey = Join-Path $resolvedDir "gateway.key"
$gwCsr = Join-Path $resolvedDir "gateway.csr"
$gwExt = Join-Path $resolvedDir "gateway.ext"
$gwCrt = Join-Path $resolvedDir "gateway.crt"
$caSrl = Join-Path $resolvedDir "ca.srl"

if ($Force -or -not (Test-Path -LiteralPath $caKey) -or -not (Test-Path -LiteralPath $caCrt)) {
    Write-Host "Generating internal EECP Root CA in $resolvedDir..."
    & openssl genrsa -out $caKey 4096
    if ($LASTEXITCODE -ne 0) { throw "Failed to generate Root CA key" }
    & openssl req -x509 -new -nodes -key $caKey -sha256 -days 3650 -out $caCrt -subj "/CN=EECP-Internal-Root-CA"
    if ($LASTEXITCODE -ne 0) { throw "Failed to generate Root CA certificate" }
}

$sans = [System.Collections.Generic.List[string]]::new()
$sans.Add("DNS:localhost")
$sans.Add("DNS:gateway-backend-tls")
$sans.Add("IP:127.0.0.1")

foreach ($ip in $ServerIPs) {
    $trimmed = $ip.Trim()
    if ($trimmed -and -not $sans.Contains("IP:$trimmed")) {
        $sans.Add("IP:$trimmed")
    }
}
foreach ($hostName in $ServerHosts) {
    $trimmed = $hostName.Trim()
    if ($trimmed -and -not $sans.Contains("DNS:$trimmed")) {
        $sans.Add("DNS:$trimmed")
    }
}

$extContent = @"
basicConstraints=CA:FALSE
keyUsage=digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth
subjectAltName=$($sans -join ",")
"@
Set-Content -LiteralPath $gwExt -Value $extContent -Encoding Ascii

Write-Host "Generating Gateway/Backend TLS certificate with SANs: $($sans -join ', ')"
& openssl req -new -nodes -newkey rsa:2048 -keyout $gwKey -out $gwCsr -subj "/CN=eecp-gateway"
if ($LASTEXITCODE -ne 0) { throw "Failed to generate Gateway CSR" }

& openssl x509 -req -in $gwCsr -CA $caCrt -CAkey $caKey -CAcreateserial -out $gwCrt -days $Days -sha256 -extfile $gwExt
if ($LASTEXITCODE -ne 0) { throw "Failed to sign Gateway certificate" }

Remove-Item -LiteralPath $gwCsr, $gwExt, $caSrl -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Certificates generated successfully:"
Write-Host "  CA Cert (distribute to workstations): $caCrt"
Write-Host "  Gateway Cert (server): $gwCrt"
Write-Host "  Gateway Key  (server): $gwKey"
