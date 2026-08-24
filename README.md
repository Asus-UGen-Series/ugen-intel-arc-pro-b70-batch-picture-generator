# ComfyUI for Intel Arc Pro B70

[繁體中文](README_zh-tw.md)

This is a standard, self-contained ComfyUI source checkout configured for Intel Arc XPU. It does **not** require ASUS UGen Console, a vendor runtime, or a fixed installation path. Clone it anywhere, create a local Python virtual environment, and run ComfyUI normally.

It also includes a resumable Z-Image Turbo batch tool for generating large datasets through ComfyUI's standard HTTP API.

> [!IMPORTANT]
> Before using an Intel Arc GPU, open the motherboard BIOS/UEFI and **enable `Above 4G Decoding`**, **enable `Re-Size BAR Support` / Resizable BAR**, and **disable `CSM` / Legacy Mode**. Boot Windows in UEFI mode. ComfyUI may still start when these options are unavailable, but generation performance can be substantially slower.

## What is included

- ComfyUI 0.27.0 source code
- Native PyTorch XPU support for Intel Arc GPUs
- `comfy-aimdo` DynamicVRAM support from `requirements.txt`
- A Z-Image Turbo API workflow
- Resumable, retryable one-prompt-per-image batch generation

Python environments, model weights, generated images, CSV results, and machine-specific runtime files are intentionally excluded from Git.

## Check the GPU and BIOS configuration

Before installing, run the included diagnostic with any Windows Python installation. Keep `check_rebar.exe` beside the Python file:

```powershell
python check_gpu_bios_exe.py
```

It checks the following recommended conditions:

- UEFI boot mode
- At least 4 GiB of dedicated VRAM
- A GPU PCI memory mapping above the 4 GiB address boundary, which indicates Above 4G Decoding
- The effective Resizable BAR state reported by the Intel graphics driver

Windows does not expose every motherboard's literal BIOS-menu switch through one universal API. The checker therefore reports the settings that are **effectively available to Windows and ComfyUI**, not the text currently selected in BIOS. Above 4G Decoding is inferred from the raw Plug and Play memory resources assigned to the GPU. `check_gpu_bios_exe.py` launches `check_rebar.exe` without a shell and follows its documented exit codes exactly: `0` means enabled, `1` means disabled, and `2` means the check failed. The executable identifies Intel Arc Pro B70 and judges ReBAR from the largest raw PCI memory resource. The Python wrapper does not override or reinterpret that result.

For troubleshooting, the alternative `check_gpu_bios.py` does not launch the exe; it queries the Intel driver through `IntelControlLib.dll` instead.

Windows WDDM 2 and later can renegotiate a supported GPU BAR after firmware initialization. Consequently, disabling only the BIOS `Re-Size BAR Support` switch may still produce `Resizable BAR (effective): enabled` if Windows successfully enabled it at runtime. That is not a claim about the BIOS-menu value; it means ReBAR is effective for the running Windows session. If the result is unexpected, fully power off and cold boot, then compare it with Intel Graphics Software or Intel Driver & Support Assistant. See the [Microsoft ReBAR driver documentation](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/resizable-bar-support), [Intel IGCL PCI API](https://intel.github.io/drivers.gpu.control-library/Control/api.html#pci), and [Intel setup guidance](https://www.intel.com/content/www/us/en/support/articles/000090831/graphics.html).

If your BIOS does not provide Above 4G Decoding or Resizable BAR, you can still install and run ComfyUI. The checker only prints a warning and does not block startup. However, generation may be **substantially slower**, especially on Intel Arc discrete GPUs. For the best performance, disable CSM/Legacy Mode, use UEFI boot mode, and enable both `Above 4G Decoding` and `Re-Size BAR Support` (sometimes named Smart Access Memory or Clever Access Memory) when those options are available.

After setup, run the complete compute verification from the project's own virtual environment:

```powershell
.\.venv\Scripts\python.exe check_gpu_bios_exe.py --compute
```

This allocates tensors on the detected B70, performs a real `torch.xpu` matrix multiplication, synchronizes the GPU, and confirms that the result and allocated VRAM belong to the B70. It is an objective test against silent CPU fallback. If ComfyUI is already running at `127.0.0.1:8188`, the same command also verifies that `/system_stats` reports the B70 as ComfyUI's compute device.

For detailed PCI addresses and BAR sizes, add `--verbose`:

```powershell
.\.venv\Scripts\python.exe check_gpu_bios_exe.py --compute --verbose
```

## Quick start on Windows with Intel Arc

Prerequisites:

- A current Intel Arc graphics driver
- Git
- 64-bit Python 3.10-3.14 available as `python`

Clone your GitHub repository, open PowerShell in the cloned folder, and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\start_comfyui.ps1
```

On the first run, `start_comfyui.ps1` automatically calls `setup_windows_xpu.ps1`, creates `.venv` inside the clone, installs the official PyTorch XPU wheels and `requirements.txt`, verifies that Intel XPU is available, and then starts ComfyUI. This can take several minutes and requires internet access. Later runs reuse the existing environment. To select another Python installation, use `.\start_comfyui.ps1 -Python C:\path\to\python.exe`.

You may run `.\setup_windows_xpu.ps1` separately when you only want to install or repair the environment.

Open <http://127.0.0.1:8188> after ComfyUI starts.

To pass normal ComfyUI arguments:

```powershell
.\start_comfyui.ps1 --port 8188 --listen 0.0.0.0
```

Only use `--listen 0.0.0.0` on a trusted network. ComfyUI listens on the local computer by default.

### Manual installation

The scripts are optional. These equivalent commands use an ordinary local virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/xpu
python -m pip install -r requirements.txt
python main.py
```

To verify that PyTorch sees the Intel GPU:

```powershell
.\.venv\Scripts\python.exe -c "import torch; print(torch.xpu.is_available()); print(torch.xpu.get_device_name(0) if torch.xpu.is_available() else 'XPU not detected')"
```

You may also inspect ComfyUI after startup:

```powershell
Invoke-RestMethod http://127.0.0.1:8188/system_stats | ConvertTo-Json -Depth 8
```

## Z-Image Turbo models

Download the model files from [Comfy-Org/z_image_turbo](https://huggingface.co/Comfy-Org/z_image_turbo) and place them as follows:

```text
models/
├── diffusion_models/
│   └── z_image_turbo_bf16.safetensors
├── text_encoders/
│   └── qwen_3_4b.safetensors
└── vae/
    └── ae.safetensors
```

Model files are not stored in this repository.

In the ComfyUI interface, you can load the built-in template at:

```text
Image generation and editing
└── Text to image
    └── Text to Image (Z-Image-Turbo)
```

## Generate a large dataset

The tools in `scripts/zimage_b70` communicate only with the standard ComfyUI API at `http://127.0.0.1:8188`. They do not depend on a desktop application or vendor runtime. Start ComfyUI first and leave it running; the first launch installs `.venv` automatically:

```powershell
.\start_comfyui.ps1
```

The repository includes `prompts_10000.txt`. To replace it with 10,000 newly generated deterministic prompts:

```powershell
.\.venv\Scripts\python.exe scripts\zimage_b70\make_prompts.py --count 10000
```

After <http://127.0.0.1:8188> opens, use a second PowerShell window to test the first 10 images:

```powershell
.\.venv\Scripts\python.exe scripts\zimage_b70\batch_generate.py scripts\zimage_b70\prompts_10000.txt `
  --limit 10 `
  --results scripts\zimage_b70\test_results.csv
```

Then run the complete batch:

```powershell
.\.venv\Scripts\python.exe scripts\zimage_b70\batch_generate.py scripts\zimage_b70\prompts_10000.txt
```

Images are written below `output/visual_search/`. Progress is recorded in `scripts/zimage_b70/results.csv`; successful IDs are skipped when the same command is restarted. Both locations are ignored by Git.

For your own dataset, provide a UTF-8 text file with one prompt per line. See [the batch-tool documentation](scripts/zimage_b70/README.md) for all options.

## Other GPUs and platforms

The repository is still standard ComfyUI. NVIDIA, AMD, CPU, Linux, and macOS users can install the PyTorch build appropriate for their platform, then run:

```text
python -m pip install -r requirements.txt
python main.py
```

See the [official ComfyUI installation guide](https://github.com/Comfy-Org/ComfyUI#installing) for current platform-specific PyTorch commands.

## Upstream and license

This project is based on [ComfyUI](https://github.com/Comfy-Org/ComfyUI). ComfyUI and downloaded models remain subject to their respective licenses. Model weights and generated datasets are not included.
