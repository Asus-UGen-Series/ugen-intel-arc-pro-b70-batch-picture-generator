#!/usr/bin/env python3
"""Sequential, resumable Z-Image Turbo generation through the ComfyUI HTTP API."""

from __future__ import annotations

import argparse
import copy
import csv
import json
import random
import statistics
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path


HERE = Path(__file__).resolve().parent
CSV_FIELDS = [
    "id", "status", "filename", "prompt", "seed", "width", "height",
    "steps", "elapsed_seconds", "prompt_id", "error",
]


def request_json(url: str, method: str = "GET", payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def require_device(stats: dict, expected: str) -> str:
    """Fail before queueing work unless ComfyUI reports the expected GPU."""
    names = [str(device.get("name", "")) for device in stats.get("devices", [])]
    match = next((name for name in names if expected.casefold() in name.casefold()), "")
    if not match:
        detected = ", ".join(names) if names else "no compute devices"
        raise RuntimeError(f"Required device {expected!r} not found; detected: {detected}")
    return match


def load_prompts(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise ValueError(
            f"Prompt file not found: {path}\n"
            "Generate it with: python .\\make_prompts.py --count 10000"
        ) from exc
    except OSError as exc:
        raise ValueError(f"Could not read prompt file {path}: {exc}") from exc
    prompts = [line.strip() for line in text.splitlines()]
    prompts = [p for p in prompts if p and not p.startswith("#")]
    if not prompts:
        raise ValueError(f"No prompts found in {path}")
    return prompts


def completed_ids(path: Path) -> set[int]:
    if not path.exists():
        return set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return {int(row["id"]) for row in csv.DictReader(handle) if row["status"] == "ok"}


def append_result(path: Path, row: dict) -> None:
    new_file = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerow(row)


def queue_and_wait(server: str, workflow: dict, poll_seconds: float) -> tuple[str, dict, float]:
    started = time.perf_counter()
    queued = request_json(
        f"{server}/prompt", "POST", {"prompt": workflow, "client_id": str(uuid.uuid4())}
    )
    prompt_id = queued["prompt_id"]
    while True:
        history = request_json(f"{server}/history/{prompt_id}")
        if prompt_id in history:
            return prompt_id, history[prompt_id], time.perf_counter() - started
        time.sleep(poll_seconds)


def output_filename(history: dict) -> str:
    for output in history.get("outputs", {}).values():
        images = output.get("images", [])
        if images:
            image = images[0]
            subfolder = image.get("subfolder", "")
            return str(Path(subfolder) / image["filename"]) if subfolder else image["filename"]
    return ""


def is_success(history: dict) -> bool:
    return history.get("status", {}).get("status_str") == "success"


def history_error(history: dict) -> str:
    messages = history.get("status", {}).get("messages", [])
    return json.dumps(messages[-1], ensure_ascii=False) if messages else "unknown error"


def configure(base: dict, prompt: str, index: int, args: argparse.Namespace, prefix: str) -> dict:
    workflow = copy.deepcopy(base)
    workflow["4"]["inputs"]["text"] = prompt
    workflow["6"]["inputs"].update(width=args.width, height=args.height, batch_size=1)
    workflow["8"]["inputs"].update(seed=args.seed_base + index, steps=args.steps)
    workflow["10"]["inputs"]["filename_prefix"] = prefix
    return workflow


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompts", type=Path, help="UTF-8 text file, one prompt per line")
    parser.add_argument("--server", default="http://127.0.0.1:8188")
    parser.add_argument("--workflow", type=Path, default=HERE / "zimage_turbo_api.json")
    parser.add_argument("--results", type=Path, default=HERE / "results.csv")
    parser.add_argument("--output-prefix", default="visual_search")
    parser.add_argument("--width", type=int, default=1024)
    parser.add_argument("--height", type=int, default=1024)
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--seed-base", type=int, default=700000)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0, help="0 means all prompts")
    parser.add_argument("--poll-seconds", type=float, default=0.2)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument(
        "--require-device",
        default="",
        help="Refuse to queue images unless /system_stats contains this device name",
    )
    args = parser.parse_args()

    args.server = args.server.rstrip("/")
    try:
        prompts = load_prompts(args.prompts)
    except ValueError as exc:
        parser.exit(2, f"ERROR: {exc}\n")

    try:
        base = json.loads(args.workflow.read_text(encoding="utf-8"))
    except FileNotFoundError:
        parser.exit(2, f"ERROR: Workflow file not found: {args.workflow}\n")
    except (OSError, ValueError) as exc:
        parser.exit(2, f"ERROR: Could not load workflow {args.workflow}: {exc}\n")

    try:
        stats = request_json(f"{args.server}/system_stats")
    except (OSError, ValueError, urllib.error.URLError) as exc:
        parser.exit(
            2,
            f"ERROR: Cannot connect to ComfyUI at {args.server}.\n"
            "Start ComfyUI first from the repository root:\n"
            "  .\\start_comfyui.ps1\n"
            f"Then open {args.server} in a browser and retry.\n"
            f"Details: {exc}\n",
        )
    if args.require_device:
        try:
            device = require_device(stats, args.require_device)
        except RuntimeError as exc:
            parser.exit(2, f"ERROR: {exc}\n")
        print(f"Verified compute device: {device}")
    if args.limit:
        prompts = prompts[: args.limit]
    done = completed_ids(args.results)

    if not done and args.warmup:
        print(f"Warm-up: {args.warmup} images (excluded from results)")
        for n in range(args.warmup):
            workflow = configure(base, prompts[n % len(prompts)], n, args, "benchmark_warmup/warmup")
            _, history, elapsed = queue_and_wait(args.server, workflow, args.poll_seconds)
            if not is_success(history):
                raise RuntimeError(f"Warm-up failed: {history_error(history)}")
            print(f"  warm-up {n + 1}/{args.warmup}: {elapsed:.3f}s")

    elapsed_ok: list[float] = []
    for index, prompt in enumerate(prompts, start=1):
        if index in done:
            continue
        prefix = f"{args.output_prefix}/{index:08d}"
        last_error = ""
        for attempt in range(args.retries + 1):
            try:
                workflow = configure(base, prompt, index, args, prefix)
                prompt_id, history, elapsed = queue_and_wait(args.server, workflow, args.poll_seconds)
                if not is_success(history):
                    raise RuntimeError(history_error(history))
                filename = output_filename(history)
                append_result(args.results, {
                    "id": index, "status": "ok", "filename": filename, "prompt": prompt,
                    "seed": args.seed_base + index, "width": args.width, "height": args.height,
                    "steps": args.steps, "elapsed_seconds": f"{elapsed:.6f}",
                    "prompt_id": prompt_id, "error": "",
                })
                elapsed_ok.append(elapsed)
                rate = 1.0 / statistics.mean(elapsed_ok)
                print(f"[{index}/{len(prompts)}] {elapsed:.3f}s | running {rate:.3f} img/s | {filename}")
                break
            except (OSError, KeyError, RuntimeError, urllib.error.URLError) as exc:
                last_error = str(exc)
                print(f"[{index}/{len(prompts)}] attempt {attempt + 1} failed: {last_error}")
                if attempt < args.retries:
                    time.sleep(1)
        else:
            append_result(args.results, {
                "id": index, "status": "failed", "filename": "", "prompt": prompt,
                "seed": args.seed_base + index, "width": args.width, "height": args.height,
                "steps": args.steps, "elapsed_seconds": "", "prompt_id": "", "error": last_error,
            })

    if elapsed_ok:
        mean = statistics.mean(elapsed_ok)
        print(f"Completed this run: {len(elapsed_ok)} images, mean {mean:.3f}s/image, {1 / mean:.3f} img/s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
