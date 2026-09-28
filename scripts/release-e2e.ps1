$ErrorActionPreference = "Stop"
for ($run = 1; $run -le 3; $run++) {
    Write-Host "EECP release E2E run $run/3"
    uv run pytest apps/api/test/integration/test_phase6_full_vertical.py apps/api/test/integration/test_phase7_reliability.py -q
    if ($LASTEXITCODE -ne 0) { throw "Release E2E run $run failed" }
}
