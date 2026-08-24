[CmdletBinding()]
param(
    [string]$Python = "python",
    [switch]$Nightly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = $PSScriptRoot
$VenvRoot = Join-Path $ProjectRoot ".venv"
$VenvPython = Join-Path $VenvRoot "Scripts\python.exe"
$Requirements = Join-Path $ProjectRoot "requirements.txt"

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList,
        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    & $FilePath @ArgumentList
    $ExitCode = $LASTEXITCODE
    if ($ExitCode -ne 0) {
        throw "$Description failed (exit code $ExitCode)."
    }
}

try {
    if ($env:OS -ne "Windows_NT") {
        throw "This setup script supports Windows only."
    }
    if (-not (Test-Path -LiteralPath $Requirements -PathType Leaf)) {
        throw "requirements.txt was not found in $ProjectRoot."
    }

    $PythonCommand = Get-Command -Name $Python -ErrorAction SilentlyContinue
    if ($null -eq $PythonCommand) {
        throw "Python was not found as '$Python'. Install 64-bit Python 3.10-3.14 or pass -Python with its full path."
    }

    $VersionOutput = @(& $Python -c "import struct, sys; print(f'{sys.version_info.major}.{sys.version_info.minor}'); print(struct.calcsize('P') * 8)")
    if ($LASTEXITCODE -ne 0 -or $VersionOutput.Count -lt 2) {
        throw "Could not inspect Python using '$Python'."
    }
    $PythonVersion = [version]$VersionOutput[-2]
    $PythonBits = [int]$VersionOutput[-1]
    if ($PythonVersion -lt [version]"3.10" -or $PythonVersion -ge [version]"3.15") {
        throw "Python $PythonVersion is unsupported. Install 64-bit Python 3.10-3.14."
    }
    if ($PythonBits -ne 64) {
        throw "A 64-bit Python installation is required; detected $PythonBits-bit."
    }
    Write-Host "Using Python $PythonVersion ($PythonBits-bit): $($PythonCommand.Source)"

    if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
        Write-Host "Creating local .venv..."
        Invoke-CheckedCommand -FilePath $Python -ArgumentList @("-m", "venv", $VenvRoot) -Description "Virtual environment creation"
    } else {
        Write-Host "Using existing local .venv."
    }

    Invoke-CheckedCommand -FilePath $VenvPython -ArgumentList @("-m", "pip", "install", "--upgrade", "pip") -Description "pip upgrade"

    $TorchIndex = "https://download.pytorch.org/whl/xpu"
    $TorchArguments = @("-m", "pip", "install")
    if ($Nightly) {
        $TorchArguments += "--pre"
        $TorchIndex = "https://download.pytorch.org/whl/nightly/xpu"
    }
    $TorchArguments += @("torch", "torchvision", "torchaudio", "--index-url", $TorchIndex)

    Write-Host "Installing PyTorch XPU from $TorchIndex..."
    Invoke-CheckedCommand -FilePath $VenvPython -ArgumentList $TorchArguments -Description "PyTorch XPU installation"

    Write-Host "Installing ComfyUI dependencies..."
    Invoke-CheckedCommand -FilePath $VenvPython -ArgumentList @("-m", "pip", "install", "-r", $Requirements) -Description "ComfyUI dependency installation"

    Write-Host "Checking Intel XPU availability..."
    Invoke-CheckedCommand -FilePath $VenvPython -ArgumentList @(
        "-c",
        "import torch; ok = hasattr(torch, 'xpu') and torch.xpu.is_available(); print('PyTorch:', torch.__version__); print('XPU available:', ok); print('Device:', torch.xpu.get_device_name(0) if ok else 'not detected'); raise SystemExit(0 if ok else 1)"
    ) -Description "Intel XPU verification"

    Write-Host "Setup complete." -ForegroundColor Green
    Write-Host "Start ComfyUI with: .\start_comfyui.ps1"
} catch {
    Write-Host ""
    Write-Host "SETUP FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Fix the message above, then run .\setup_windows_xpu.ps1 again."
    return
}
