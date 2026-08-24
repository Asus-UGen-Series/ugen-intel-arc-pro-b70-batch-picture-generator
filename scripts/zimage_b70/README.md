# Z-Image Turbo batch generation (ComfyUI API)

This uses a standard running ComfyUI server at `http://127.0.0.1:8188`; no
vendor runtime or fixed installation path is required. Its API workflow
matches the bundled **Text to Image (Z-Image-Turbo)** blueprint: 1024x1024, 8
steps, CFG 1, `res_multistep`, `simple`, AuraFlow shift 3, and batch size 1.

PowerShell quick start from the ComfyUI directory (using the repository's local
virtual environment):

```powershell
.\start_comfyui.ps1
```

The first launch creates and installs `.venv` automatically, which can take
several minutes. Leave that PowerShell window open. After
<http://127.0.0.1:8188> loads, open a second PowerShell window in the ComfyUI
directory and run:

```powershell
.\.venv\Scripts\python.exe scripts\zimage_b70\batch_generate.py scripts\zimage_b70\prompts_10000.txt --warmup 3
```

`prompts_10000.txt` is included. To replace it with a newly generated set, run
`.\.venv\Scripts\python.exe scripts\zimage_b70\make_prompts.py --count 10000`.

Test only ten images first:

```powershell
.\.venv\Scripts\python.exe scripts\zimage_b70\batch_generate.py scripts\zimage_b70\prompts_10000.txt --limit 10 --results scripts\zimage_b70\test_results.csv
```

Images are saved under `output/visual_search`. Per-image prompt, seed, status,
filename, and end-to-end elapsed time are appended to `results.csv`. Successful
IDs are skipped when the same command is restarted, so interrupted runs resume.

To use your own prompts, provide a UTF-8 text file containing one prompt per
line. Keep `batch_size` at 1 for the different-prompt dataset. Use separate result
files when comparing resolutions or step counts, for example:

```powershell
.\.venv\Scripts\python.exe scripts\zimage_b70\batch_generate.py my_prompts.txt --limit 100 --width 1024 --height 1024 --steps 8 --results benchmark_1024.csv
```
