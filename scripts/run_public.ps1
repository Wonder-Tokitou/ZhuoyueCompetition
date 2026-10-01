param(
    [switch]$NamedTunnel,
    [string]$PublicHostname,
    [string]$TunnelTokenFile = (Join-Path $PSScriptRoot '..\private\public-test\cloudflared-token.txt')
)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Project virtual environment is missing. Run start.bat once first.'
}

$arguments = @('-u', 'scripts\run_public.py')
if ($NamedTunnel) {
    if (-not $PublicHostname) { $PublicHostname = $env:PUBLIC_HOSTNAME }
    if (-not $PublicHostname) { throw 'Pass -PublicHostname with the Cloudflare-published hostname.' }
    if (-not (Test-Path -LiteralPath $TunnelTokenFile -PathType Leaf)) {
        throw "Named tunnel token file not found: $TunnelTokenFile"
    }
    $arguments += @('--public-hostname', $PublicHostname, '--tunnel-token-file', [IO.Path]::GetFullPath($TunnelTokenFile))
}

Push-Location $projectRoot
try { & $python @arguments; exit $LASTEXITCODE }
finally { Pop-Location }
