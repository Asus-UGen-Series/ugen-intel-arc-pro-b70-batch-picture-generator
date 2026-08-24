# 適用於 Intel Arc Pro B70 的 ComfyUI

[English](README.md)

這是一份標準、可獨立安裝的 ComfyUI 原始碼，針對 Intel Arc XPU 使用情境整理。它**不需要** ASUS UGen Console、廠商 runtime 或固定安裝路徑；一般使用者可以 clone 到任意資料夾，建立自己的 Python 虛擬環境後直接執行 ComfyUI。

專案另外提供透過 ComfyUI 標準 HTTP API 運作的 Z-Image Turbo 可續跑批次工具。

> [!IMPORTANT]
> 使用 Intel Arc GPU 前，請先進入主機板 BIOS／UEFI：**開啟 `Above 4G Decoding`**、**開啟 `Re-Size BAR Support`／Resizable BAR**，並且**關閉 `CSM`／Legacy Mode**，讓 Windows 使用 UEFI 模式開機。若 BIOS 沒有這些選項，ComfyUI 仍可能可以啟動，但生成速度可能會大幅下降。

## 包含內容

- ComfyUI 0.27.0 原始碼
- Intel Arc GPU 的原生 PyTorch XPU 支援
- `requirements.txt` 內的 `comfy-aimdo` DynamicVRAM 支援
- Z-Image Turbo API workflow
- 每張圖片使用獨立 prompt、可重試且可續跑的批次生成工具

Python 環境、模型權重、生成圖片、CSV 結果與特定電腦的 runtime 檔案都不會加入 Git。

## 檢查 GPU 與 BIOS 設定

安裝前，使用任一 Windows Python 執行專案附帶的檢查程式，並將 `check_rebar.exe` 與 Python 檔放在同一個目錄：

```powershell
python check_gpu_bios_exe.py
```

程式會檢查以下建議條件：

- 使用 UEFI 開機模式
- 至少 4 GiB 獨立 VRAM
- GPU 的 PCI 記憶體位址是否超過 4 GiB 邊界，用來判斷 Above 4G Decoding
- Intel 顯示驅動程式回報的 Resizable BAR 實際生效狀態

Windows 沒有統一介面可以直接讀取所有主機板 BIOS 選單中的開關值，因此檢查程式回報的是 **Windows 與 ComfyUI 實際可用的狀態**，不是 BIOS 畫面目前選了哪個文字。Above 4G Decoding 會依 Windows Plug and Play 分配給 GPU 的原始 PCI 記憶體資源推斷。`check_gpu_bios_exe.py` 會在不經過 shell 的情況下啟動 `check_rebar.exe`，並完全按照 exe 定義的 exit code 判定：`0` 代表已啟用、`1` 代表未啟用、`2` 代表檢查失敗。該 exe 會辨識 Intel Arc Pro B70，並依最大的原始 PCI 記憶體資源判斷 ReBAR；Python 不會覆寫或重新解讀它的結果。

若要交叉除錯，也可以執行不啟動 exe 的 `check_gpu_bios.py`；該版本會改由 Intel 驅動的 `IntelControlLib.dll` 查詢。

Windows WDDM 2 之後可以在韌體初始化完成後，重新協商支援此功能的 GPU BAR。因此只關閉 BIOS 的 `Re-Size BAR Support`，Windows 仍可能在執行階段成功啟用，檢查結果就會顯示 `Resizable BAR (effective): enabled`。這不是宣稱 BIOS 選單仍為開啟，而是代表本次 Windows 執行階段的 ReBAR 確實有效。若結果與預期不同，請完整關機並冷開機後再測，並與 Intel Graphics Software 或 Intel Driver & Support Assistant 交叉確認。相關說明可參考 [Microsoft ReBAR 驅動文件](https://learn.microsoft.com/en-us/windows-hardware/drivers/display/resizable-bar-support)、[Intel IGCL PCI API](https://intel.github.io/drivers.gpu.control-library/Control/api.html#pci) 與 [Intel 設定指南](https://www.intel.com/content/www/us/en/support/articles/000090831/graphics.html)。

如果 BIOS 沒有提供 Above 4G Decoding 或 Resizable BAR，**仍然可以安裝並執行 ComfyUI**；檢查程式只會顯示警告，不會阻止啟動。不過生成速度可能會**大幅下降**，Intel Arc 獨立顯示卡尤其明顯。若 BIOS 有相關選項，建議停用 CSM／Legacy Mode、使用 UEFI 開機，並啟用 `Above 4G Decoding` 與 `Re-Size BAR Support`；後者也可能稱為 Smart Access Memory 或 Clever Access Memory。

完成安裝後，請使用專案自己的虛擬環境執行完整運算驗證：

```powershell
.\.venv\Scripts\python.exe check_gpu_bios_exe.py --compute
```

這項測試會在偵測到的 B70 上配置 tensor、實際執行 `torch.xpu` 矩陣乘法、等待 GPU 同步完成，並確認結果 tensor 與 VRAM 配置都屬於 B70，可客觀排除程式偷偷退回 CPU 執行的情況。如果 ComfyUI 已在 `127.0.0.1:8188` 執行，同一個指令也會檢查 `/system_stats`，確認 ComfyUI 回報的運算裝置是 B70。

若要顯示完整 PCI 位址與 BAR 大小，可加入 `--verbose`：

```powershell
.\.venv\Scripts\python.exe check_gpu_bios_exe.py --compute --verbose
```

## Windows + Intel Arc 快速安裝

請先準備：

- 最新 Intel Arc 顯示卡驅動程式
- Git
- 可用 `python` 指令啟動的 64-bit Python 3.10-3.14

Clone 你的 GitHub repository，在 clone 後的資料夾開啟 PowerShell：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\start_comfyui.ps1
```

第一次執行時，`start_comfyui.ps1` 會自動呼叫 `setup_windows_xpu.ps1`，在專案內建立 `.venv`、安裝官方 PyTorch XPU 與 `requirements.txt`、確認 Intel XPU 可用，然後啟動 ComfyUI。首次安裝可能需要數分鐘且必須能連線網路；之後會直接重用現有環境。若要指定其他 Python，請使用 `.\start_comfyui.ps1 -Python C:\path\to\python.exe`。

如果只想安裝或修復環境，也可以另外執行 `.\setup_windows_xpu.ps1`。

ComfyUI 啟動後開啟 <http://127.0.0.1:8188>。

也可以傳入一般 ComfyUI 參數：

```powershell
.\start_comfyui.ps1 --port 8188 --listen 0.0.0.0
```

只有在可信任的區域網路才使用 `--listen 0.0.0.0`；預設只允許本機連線。

### 手動安裝

腳本不是必要條件。以下指令會建立一般的本機虛擬環境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/xpu
python -m pip install -r requirements.txt
python main.py
```

確認 PyTorch 是否偵測到 Intel GPU：

```powershell
.\.venv\Scripts\python.exe -c "import torch; print(torch.xpu.is_available()); print(torch.xpu.get_device_name(0) if torch.xpu.is_available() else 'XPU not detected')"
```

ComfyUI 啟動後也可以檢查系統資訊：

```powershell
Invoke-RestMethod http://127.0.0.1:8188/system_stats | ConvertTo-Json -Depth 8
```

## Z-Image Turbo 模型

從 [Comfy-Org/z_image_turbo](https://huggingface.co/Comfy-Org/z_image_turbo) 下載模型，放入以下位置：

```text
models/
├── diffusion_models/
│   └── z_image_turbo_bf16.safetensors
├── text_encoders/
│   └── qwen_3_4b.safetensors
└── vae/
    └── ae.safetensors
```

模型檔案不會存入這個 repository。

你也可以在 ComfyUI 介面載入內建範本：

```text
Image generation and editing
└── Text to image
    └── Text to Image (Z-Image-Turbo)
```

## 生成大型資料集

`scripts/zimage_b70` 內的工具只會呼叫 `http://127.0.0.1:8188` 的標準 ComfyUI API，不依賴 Desktop 應用程式或任何廠商 runtime。請先啟動 ComfyUI，並保持該 PowerShell 視窗開啟；首次啟動會自動安裝 `.venv`：

```powershell
.\start_comfyui.ps1
```

專案已附上 `prompts_10000.txt`。若要重新產生一組 10,000 個固定且不重複的 prompts：

```powershell
.\.venv\Scripts\python.exe scripts\zimage_b70\make_prompts.py --count 10000
```

確認 <http://127.0.0.1:8188> 可以開啟後，在第二個 PowerShell 視窗先測試前 10 張：

```powershell
.\.venv\Scripts\python.exe scripts\zimage_b70\batch_generate.py scripts\zimage_b70\prompts_10000.txt `
  --limit 10 `
  --results scripts\zimage_b70\test_results.csv
```

確認正常後執行完整批次：

```powershell
.\.venv\Scripts\python.exe scripts\zimage_b70\batch_generate.py scripts\zimage_b70\prompts_10000.txt
```

圖片會存入 `output/visual_search/`，進度會寫入 `scripts/zimage_b70/results.csv`。相同指令重新啟動時會跳過已成功的 ID；這兩類產物都已由 Git 忽略。

也可以提供自己的 UTF-8 文字檔，每行一個 prompt。完整參數請參考[批次工具說明](scripts/zimage_b70/README.md)。

## 其他 GPU 與平台

這仍是標準 ComfyUI。NVIDIA、AMD、CPU、Linux 與 macOS 使用者只要安裝符合自己平台的 PyTorch，再執行：

```text
python -m pip install -r requirements.txt
python main.py
```

各平台目前的 PyTorch 安裝指令請參考 [ComfyUI 官方安裝說明](https://github.com/Comfy-Org/ComfyUI#installing)。

## 上游專案與授權

本專案以 [ComfyUI](https://github.com/Comfy-Org/ComfyUI) 為基礎。ComfyUI 與下載的模型分別適用各自的授權條款；repository 不包含模型權重與生成資料集。
