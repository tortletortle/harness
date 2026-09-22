param(
    [switch]$SkipTests,
    [switch]$SkipRelease,
    [switch]$Offline,
    [switch]$Doctor,
    [ValidateSet('auto', 'msvc', 'gnu')][string]$Toolchain = 'auto',
    [string]$ZigPath
)

$ErrorActionPreference = 'Stop'
$oldPath = $env:PATH
$oldLocation = Get-Location
function Invoke-Cargo([string[]]$CargoArgs) {
    # Rust progress is stderr, not a PowerShell failure. Trust its exit code.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & cargo @CargoArgs
        if ($LASTEXITCODE -ne 0) { throw "cargo failed (exit $LASTEXITCODE): $CargoArgs" }
    } finally { $ErrorActionPreference = $previous }
}
try {
    Set-Location (Join-Path $PSScriptRoot '..')
    if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) { throw 'Install Rust via rustup first.' }
    $link = Get-Command link.exe -ErrorAction SilentlyContinue
    $msvcReady = $false
    if ($link) {
        $description = (Get-Item $link.Source).VersionInfo.FileDescription
        $msvcReady = ($description -match 'Microsoft.*Linker') -and [bool]$env:LIB
    }
    if ($Toolchain -eq 'auto') { if ($msvcReady) { $Toolchain = 'msvc' } else { $Toolchain = 'gnu' } }
    $target = "x86_64-pc-windows-$Toolchain"
    $rust = "+stable-$target"
    if (-not ((& rustup toolchain list) -match "^stable-$target")) {
        throw "Missing toolchain. Run: rustup toolchain install stable-$target"
    }
    if ($Toolchain -eq 'msvc') {
        if (-not $msvcReady) {
            throw 'Microsoft linker/SDK environment is missing (Git link.exe is not a linker). Use Developer PowerShell for VS, or -Toolchain gnu with cargo-zigbuild + ziglang.'
        }
        Write-Host "MSVC linker: $($link.Source)"
    } else {
        if (-not (Get-Command cargo-zigbuild -ErrorAction SilentlyContinue)) {
            throw 'GNU build needs cargo-zigbuild. Install it (e.g. python -m pip install cargo-zigbuild ziglang).'
        }
        if ($ZigPath) {
            if (Test-Path $ZigPath -PathType Leaf) { $ZigPath = Split-Path $ZigPath }
            $env:PATH = "$ZigPath;$env:PATH"
        }
        if (-not (Get-Command zig -ErrorAction SilentlyContinue)) {
            $python = Get-Command python -ErrorAction SilentlyContinue
            if ($python) {
                $zigDir = & python -c "import importlib.util,pathlib; s=importlib.util.find_spec('ziglang'); print(pathlib.Path(s.origin).parent if s else '')"
                if ($LASTEXITCODE -eq 0 -and $zigDir -and (Test-Path (Join-Path $zigDir 'zig.exe'))) {
                    $env:PATH = "$zigDir;$env:PATH"
                }
            }
        }
        if (-not (Get-Command zig -ErrorAction SilentlyContinue)) {
            throw 'Zig not found. Install ziglang in the active Python environment or pass -ZigPath <directory>. No global PATH change is required.'
        }
        Write-Host "Zig: $((Get-Command zig).Source)"
        & zig version
        if ($LASTEXITCODE -ne 0) { throw 'Zig preflight failed.' }
    }
    Write-Host "Toolchain: $rust; target: $target"
    if ($Doctor) { return }
    $common = @('--locked')
    if ($Offline) { $common += '--offline' }
    Write-Host '[1/4] Checking formatting'
    Invoke-Cargo @('fmt', '--all', '--', '--check')

    if ($Toolchain -eq 'gnu') {
        Write-Host '[2/4] Compiling workspace with the verified Zig linker'
        $args = @($rust, 'zigbuild', '--workspace', '--target', $target, '--message-format=json') + $common
        if (-not $SkipTests) { $args += '--tests' }
        $events = Invoke-Cargo $args
        Write-Host '[3/4] Running only test executables emitted by this cargo invocation'
        if (-not $SkipTests) {
            $executables = @($events | ForEach-Object {
                try { $event = $_ | ConvertFrom-Json } catch { return }
                if ($event.reason -eq 'compiler-artifact' -and $event.profile.test -and $event.executable) { $event.executable }
            } | Sort-Object -Unique)
            if (-not $executables.Count) { throw 'Cargo emitted no test executables; refusing to use historical target binaries.' }
            foreach ($exe in $executables) {
                & $exe
                if ($LASTEXITCODE -ne 0) { throw "Test failed: $exe" }
            }
        } else { Write-Host 'Tests skipped by request.' }
    } else {
        Write-Host '[2/4] Checking workspace'
        Invoke-Cargo (@($rust, 'check', '--workspace', '--all-targets', '--target', $target) + $common)
        Write-Host '[3/4] Running tests'
        if (-not $SkipTests) { Invoke-Cargo (@($rust, 'test', '--workspace', '--target', $target) + $common) }
    }
    if (-not $SkipRelease) {
        Write-Host '[4/4] Building release executables'
        $build = 'build'
        if ($Toolchain -eq 'gnu') { $build = 'zigbuild' }
        Invoke-Cargo (@($rust, $build, '--release', '--target', $target, '-p', 'eas-gui', '-p', 'eas-server') + $common)
        Write-Host "Artifacts: target/$target/release/eas-gui.exe; target/$target/release/eas-server.exe"
    } else { Write-Host '[4/4] Release build skipped by request.' }
} finally {
    $env:PATH = $oldPath
    Set-Location $oldLocation
}
