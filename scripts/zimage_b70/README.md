# Local ComfyUI workflow batch runner

`batch_generate.py` repeatedly runs a workflow on your locally installed
ComfyUI. It does not use a cloud image service, require an API key, or upload
your prompt file. ComfyUI must be running locally, normally at
`http://127.0.0.1:8188`.

The included `zimage_turbo_workflow.json` is ready to run with the included
command. It matches ComfyUI's **Text to Image (Z-Image-Turbo)** workflow:
1024x1024, 8 steps, CFG 1, `res_multistep`, `simple`, AuraFlow shift 3, and
batch size 1.

## See every command-line option

Run `--help` whenever you need the full argument list, default values, and
examples:

```powershell
.\.venv\Scripts\python.exe scripts\zimage_b70\batch_generate.py --help
```

Important options include `--workflow`, `--results`, `--output-prefix`,
`--width`, `--height`, `--steps`, `--seed-base`, `--warmup`, `--limit`,
`--retries`, `--require-device`, and the optional workflow node overrides.

## Quick start with the included workflow

From the ComfyUI directory, start the local server:

```powershell
.\start_comfyui.ps1
```

Leave that window open. In a second PowerShell window, run:

```powershell
.\.venv\Scripts\python.exe scripts\zimage_b70\batch_generate.py scripts\zimage_b70\prompts_10000.txt --warmup 3
```

`prompts_10000.txt` is included. To generate a new prompt set:

```powershell
.\.venv\Scripts\python.exe scripts\zimage_b70\make_prompts.py --count 10000
```

Test a small run before starting all 10,000 images:

```powershell
.\.venv\Scripts\python.exe scripts\zimage_b70\batch_generate.py scripts\zimage_b70\prompts_10000.txt --limit 10 --results scripts\zimage_b70\test_results.csv
```

Images are saved under `output/visual_search`. Prompt, seed, status, filename,
elapsed time, and errors are appended to the results CSV. Successful IDs are
skipped when the same results file is reused, so an interrupted run resumes.

## Use your own workflow or image model

The bundled JSON is only the default. Use another local executable workflow
JSON with `--workflow`:

```powershell
.\.venv\Scripts\python.exe scripts\zimage_b70\batch_generate.py my_prompts.txt --workflow my_workflow.json
```

The JSON must be the node-object form accepted by a running ComfyUI: top-level
node IDs, with `class_type` and `inputs` on every node. A normal editor-layout
export containing canvas positions and links is not directly executable.

To use a different image model, copy your model files into the appropriate
ComfyUI `models` folders and change the loader filenames/settings in your JSON.
The runner is not tied to the Z-Image model name.

The runner automatically finds these editable nodes by their inputs:

- prompt text (`text`)
- image size (`width` and `height`)
- sampler (`seed` and `steps`)
- saved filename (`filename_prefix`)

If a custom workflow has multiple matching nodes, select the intended node IDs
explicitly:

```powershell
.\.venv\Scripts\python.exe scripts\zimage_b70\batch_generate.py my_prompts.txt `
  --workflow my_workflow.json `
  --prompt-node 12 --size-node 20 --sampler-node 31 --save-node 45
```

The selected nodes must expose the corresponding inputs listed above. Run
`--help` for the description and default of every option.

## Custom prompts and benchmark settings

Prompt files are UTF-8 text files with one prompt per line. Lines beginning
with `#` and empty lines are ignored. Keep the workflow batch size at 1 when
each output should use a different prompt. Use a separate results file when
comparing settings:

```powershell
.\.venv\Scripts\python.exe scripts\zimage_b70\batch_generate.py my_prompts.txt --limit 100 --width 768 --height 768 --steps 12 --results benchmark_768.csv
```
