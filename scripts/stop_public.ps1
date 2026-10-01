param([switch]$WhatIf)
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$publicRoot = Join-Path $projectRoot 'private\public-test'
$linkStatePath = Join-Path $publicRoot 'links.json'
$expectedPython = [IO.Path]::GetFullPath((Join-Path $projectRoot '.venv\Scripts\python.exe'))
$pythonProcesses = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe'")
$launchers = @($pythonProcesses | Where-Object {
    $processPython = $_.ExecutablePath
    $_.CommandLine -and $processPython -and
    [IO.Path]::GetFullPath($processPython).Equals($expectedPython, [StringComparison]::OrdinalIgnoreCase) -and
    $_.CommandLine -match 'scripts[\\/]run_public\.py(?:["\s]|$)' -and
    $_.CommandLine -notmatch '--offline-fixture|--local-only'
})
$launcherIds = @($launchers | ForEach-Object { [int]$_.ProcessId })
$targets = @($pythonProcesses | Where-Object {
    $isLauncher = $launcherIds -contains [int]$_.ProcessId
    # Windows' venv launcher can spawn the base interpreter (e.g. Miniconda).
    # Its executable path is outside the project, but its direct parent is the
    # exact project venv launcher and it runs the same public entry point.
    $isRunnerChild = $launcherIds -contains [int]$_.ParentProcessId -and
        $_.CommandLine -match 'scripts[\\/]run_public\.py(?:["\s]|$)' -and
        $_.CommandLine -notmatch '--offline-fixture|--local-only'
    $isLauncher -or $isRunnerChild
})
foreach ($target in $targets) {
    if ($WhatIf) {
        Write-Output "Verified public launcher PID: $($target.ProcessId)"
    } else {
        # The venv shim and Python child may both match. Job teardown can remove
        # the second before this loop reaches it, which is already success.
        Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Output "Stopped public launcher PID: $($target.ProcessId). Saved keys and records are retained."
    }
}
if (-not $WhatIf) {
    # Revoke stale public URLs even when the launcher was already stopped or
    # was force-closed. Never leave yesterday's Quick Tunnel advertised as live.
    $state = [ordered]@{ status = 'stopped'; teacher = $null; student = $null; switch_space = $null }
    $tempPath = Join-Path $publicRoot ('links-' + [guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::WriteAllText($tempPath, ($state | ConvertTo-Json -Depth 3), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $tempPath -Destination $linkStatePath -Force
}
if ($targets.Count -eq 0) { Write-Output 'No public launcher for this project is running.' }
