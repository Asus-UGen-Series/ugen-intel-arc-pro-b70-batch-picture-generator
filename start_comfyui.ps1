[CmdletBinding(PositionalBinding = $false)]
param(
    [string]$Python = "python",
    [switch]$Nightly,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ComfyArguments
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$SetupScript = Join-Path $ProjectRoot "setup_windows_xpu.ps1"
$MainScript = Join-Path $ProjectRoot "main.py"

function Test-ComfyEnvironment {
    if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
        return $false
    }
    try {
        & $VenvPython -c "import importlib.metadata, aiohttp, torch, torchgen, torchaudio, torchvision; assert importlib.metadata.version('torch'); raise SystemExit(0 if hasattr(torch, 'xpu') and torch.xpu.is_available() else 1)" 2>$null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

try {
    if (-not (Test-Path -LiteralPath $MainScript -PathType Leaf)) {
        throw "main.py was not found in $ProjectRoot."
    }

    if (-not (Test-ComfyEnvironment)) {
        if (-not (Test-Path -LiteralPath $SetupScript -PathType Leaf)) {
            throw "The local environment is not ready and setup_windows_xpu.ps1 is missing."
        }
        Write-Host "The local ComfyUI XPU environment is not installed yet."
        Write-Host "Running first-time setup automatically; this can take several minutes..."
        $SetupArguments = @{ Python = $Python }
        if ($Nightly) {
            $SetupArguments.Nightly = $true
        }
        & $SetupScript @SetupArguments
        if (-not (Test-ComfyEnvironment)) {
            Write-Host ""
            Write-Host "ComfyUI was not started because first-time setup is incomplete." -ForegroundColor Yellow
            Write-Host "Fix the SETUP FAILED message above, then run .\start_comfyui.ps1 again."
            return
        }
    }

    Write-Host "Starting ComfyUI..."
    Push-Location -LiteralPath $ProjectRoot
    try {
        & $VenvPython $MainScript @ComfyArguments
        $ExitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($ExitCode -ne 0) {
        Write-Host "ComfyUI exited with code $ExitCode." -ForegroundColor Red
    }
} catch {
    Write-Host ""
    Write-Host "START FAILED: $($_.Exception.Message)" -ForegroundColor Red
    return
}
