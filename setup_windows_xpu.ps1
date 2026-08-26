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

function Assert-WindowsPathCapacity {
    $LongPathsEnabled = Get-ItemPropertyValue `
        -LiteralPath "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
        -Name "LongPathsEnabled" `
        -ErrorAction SilentlyContinue

    if ($LongPathsEnabled -eq 1) {
        return
    }

    # PyTorch 2.13 XPU includes this deeply nested license directory. Legacy
    # Win32 directory creation can fail at roughly 248 characters.
    $TorchDeepPath = "Lib\site-packages\torch-2.13.0+xpu.dist-info\licenses\third_party\kineto\libkineto\third_party\dynolog\third_party\prometheus-cpp\3rdparty\civetweb\src\third_party\duktape-1.5.2"
    $ProjectedPath = Join-Path $VenvRoot $TorchDeepPath
    if ($ProjectedPath.Length -ge 248) {
        throw "Windows long-path support is disabled and this repository path is too deep for PyTorch XPU ($($ProjectedPath.Length) characters). Move or clone the repository to a short path such as C:\ComfyUIBatch, then run .\start_comfyui.ps1 again. Alternatively, enable Win32 long paths as Administrator and restart Windows; see README.md."
    }
}

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

function Test-PythonCode {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Code
    )

    # A nonzero exit is an expected Boolean result for probe commands. Some
    # PowerShell versions promote native stderr/nonzero exits into terminating
    # errors when the surrounding script uses ErrorAction=Stop.
    $PreviousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $VenvPython -c $Code 2>$null
        $CheckExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
    return $CheckExitCode -eq 0
}

function Test-TorchInstallation {
    param(
        [switch]$RequireXpu
    )

    $XpuCheck = if ($RequireXpu) {
        " and hasattr(torch, 'xpu') and torch.xpu.is_available()"
    } else {
        ""
    }
    $Check = "import importlib.metadata, torch, torchgen, torchaudio, torchvision; version = importlib.metadata.version('torch'); ok = bool(version)$XpuCheck; raise SystemExit(0 if ok else 1)"

    return Test-PythonCode -Code $Check
}

try {
    if ($env:OS -ne "Windows_NT") {
        throw "This setup script supports Windows only."
    }
    if (-not (Test-Path -LiteralPath $Requirements -PathType Leaf)) {
        throw "requirements.txt was not found in $ProjectRoot."
    }

    Assert-WindowsPathCapacity

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

    # A cancelled or path-truncated pip run can leave a torch directory and
    # dist-info behind while omitting torchgen or METADATA. In that state pip
    # reports "requirement already satisfied" and never repairs it unless the
    # damaged installation metadata is bypassed explicitly.
    $TorchFilesExist = Test-PythonCode -Code "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('torch') else 1)"
    if ($TorchFilesExist -and -not (Test-TorchInstallation)) {
        Write-Host "The existing PyTorch installation is incomplete; repairing it..." -ForegroundColor Yellow
        # --ignore-installed is intentional: an interrupted install may have no
        # valid METADATA or RECORD, so pip cannot reliably uninstall it first.
        $TorchArguments = @("-m", "pip", "install", "--ignore-installed", "--no-cache-dir") + $TorchArguments[3..($TorchArguments.Count - 1)]
    }

    Write-Host "Installing PyTorch XPU from $TorchIndex..."
    Invoke-CheckedCommand -FilePath $VenvPython -ArgumentList $TorchArguments -Description "PyTorch XPU installation"

    if (-not (Test-TorchInstallation)) {
        throw "PyTorch installation is incomplete (torch, torchgen, torchvision, torchaudio, or package metadata could not be loaded). Run this script again to force a clean package reinstall."
    }

    Write-Host "Installing ComfyUI dependencies..."
    Invoke-CheckedCommand -FilePath $VenvPython -ArgumentList @("-m", "pip", "install", "-r", $Requirements) -Description "ComfyUI dependency installation"
    Invoke-CheckedCommand -FilePath $VenvPython -ArgumentList @("-m", "pip", "check") -Description "Python dependency compatibility check"

    Write-Host "Checking Intel XPU availability..."
    if (-not (Test-TorchInstallation -RequireXpu)) {
        throw "Intel XPU verification failed. PyTorch and its companion packages must import successfully, and torch.xpu.is_available() must return true."
    }
    Invoke-CheckedCommand -FilePath $VenvPython -ArgumentList @(
        "-c",
        "import torch; print('PyTorch:', torch.__version__); print('XPU available:', True); print('Device:', torch.xpu.get_device_name(0))"
    ) -Description "Intel XPU device report"

    Write-Host "Setup complete." -ForegroundColor Green
    Write-Host "Start ComfyUI with: .\start_comfyui.ps1"
} catch {
    Write-Host ""
    Write-Host "SETUP FAILED: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Fix the message above, then run .\setup_windows_xpu.ps1 again."
    return
}
