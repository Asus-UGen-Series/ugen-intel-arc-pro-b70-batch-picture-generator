#!/usr/bin/env python3
"""Run a local ComfyUI workflow repeatedly with prompts from a text file."""

from __future__ import annotations

import argparse
import copy
import csv
import json
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


class HelpFormatter(
    argparse.ArgumentDefaultsHelpFormatter,
    argparse.RawDescriptionHelpFormatter,
):
    """Preserve example line breaks while still displaying default values."""


class ComfyRequestError(RuntimeError):
    """A rejected local ComfyUI request, including workflow validation details."""


def format_api_error(error: urllib.error.HTTPError, raw: str) -> str:
    try:
        response = json.loads(raw)
    except ValueError:
        response = None

    details: list[str] = []
    if isinstance(response, dict):
        api_error = response.get("error", {})
        if isinstance(api_error, dict):
            message = api_error.get("message") or api_error.get("type")
            if message:
                details.append(str(message))
        for node_id, node_error in response.get("node_errors", {}).items():
            class_type = node_error.get("class_type", "unknown node")
            for item in node_error.get("errors", []):
                message = item.get("details") or item.get("message") or item.get("type")
                details.append(f"node {node_id} ({class_type}): {message}")
    if not details and raw.strip():
        details.append(raw.strip())
    suffix = "; ".join(details) if details else error.reason
    return f"ComfyUI workflow request failed (HTTP {error.code}): {suffix}"


def request_json(url: str, method: str = "GET", payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise ComfyRequestError(format_api_error(exc, raw)) from exc


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


def node_sort_key(node_id: str) -> tuple[int, int | str]:
    return (0, int(node_id)) if node_id.isdigit() else (1, node_id)


def find_workflow_node(
    workflow: dict,
    explicit_id: str,
    label: str,
    required_inputs: tuple[str, ...],
    preferred_types: tuple[str, ...],
    preferred_value: tuple[str, str] | None = None,
) -> str:
    if explicit_id:
        node = workflow.get(explicit_id)
        if not isinstance(node, dict):
            raise ValueError(f"{label} node {explicit_id!r} does not exist in the workflow")
        missing = [name for name in required_inputs if name not in node.get("inputs", {})]
        if missing:
            raise ValueError(
                f"{label} node {explicit_id!r} is missing input(s): {', '.join(missing)}"
            )
        return explicit_id

    candidates = [
        str(node_id)
        for node_id, node in workflow.items()
        if isinstance(node, dict)
        and all(name in node.get("inputs", {}) for name in required_inputs)
    ]
    if preferred_value:
        name, value = preferred_value
        matches = [
            node_id for node_id in candidates
            if workflow[node_id].get("inputs", {}).get(name) == value
        ]
        if matches:
            candidates = matches
    type_matches = [
        node_id for node_id in candidates
        if workflow[node_id].get("class_type") in preferred_types
    ]
    if type_matches:
        candidates = type_matches
    if not candidates:
        inputs = ", ".join(required_inputs)
        raise ValueError(
            f"Could not auto-detect the {label} node (required inputs: {inputs}). "
            f"Pass --{label}-node with the node ID from your workflow JSON."
        )
    return sorted(candidates, key=node_sort_key)[0]


def resolve_workflow_nodes(workflow: dict, args: argparse.Namespace) -> dict[str, str]:
    if not isinstance(workflow, dict) or not workflow:
        raise ValueError("Workflow JSON must be a non-empty object keyed by node IDs")
    return {
        "prompt": find_workflow_node(
            workflow, args.prompt_node, "prompt", ("text",),
            ("CLIPTextEncode",), ("text", "PROMPT_PLACEHOLDER"),
        ),
        "size": find_workflow_node(
            workflow, args.size_node, "size", ("width", "height"),
            ("EmptySD3LatentImage", "EmptyLatentImage"),
        ),
        "sampler": find_workflow_node(
            workflow, args.sampler_node, "sampler", ("seed", "steps"), ("KSampler",),
        ),
        "save": find_workflow_node(
            workflow, args.save_node, "save", ("filename_prefix",), ("SaveImage",),
        ),
    }


def configure(
    base: dict,
    nodes: dict[str, str],
    prompt: str,
    index: int,
    args: argparse.Namespace,
    prefix: str,
) -> dict:
    workflow = copy.deepcopy(base)
    workflow[nodes["prompt"]]["inputs"]["text"] = prompt
    size_inputs = workflow[nodes["size"]]["inputs"]
    size_inputs.update(width=args.width, height=args.height)
    if "batch_size" in size_inputs:
        size_inputs["batch_size"] = 1
    workflow[nodes["sampler"]]["inputs"].update(
        seed=args.seed_base + index, steps=args.steps
    )
    workflow[nodes["save"]]["inputs"]["filename_prefix"] = prefix
    return workflow


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=HelpFormatter,
        epilog=(
            "examples:\n"
            "  batch_generate.py prompts_10000.txt --warmup 3\n"
            "  batch_generate.py prompts.txt --limit 10 --width 768 --height 768\n"
            "  batch_generate.py prompts.txt --workflow my_workflow.json\n"
            "  batch_generate.py prompts.txt --workflow my_workflow.json --prompt-node 12"
        ),
    )
    parser.add_argument("prompts", type=Path, help="UTF-8 text file containing one prompt per line")
    parser.add_argument(
        "--server", default="http://127.0.0.1:8188",
        help="address of the locally running ComfyUI instance",
    )
    parser.add_argument(
        "--workflow", type=Path, default=HERE / "zimage_turbo_workflow.json",
        help="local executable workflow JSON; edit model loader values to use other models",
    )
    parser.add_argument("--results", type=Path, default=HERE / "results.csv", help="resumable CSV results file")
    parser.add_argument("--output-prefix", default="visual_search", help="subfolder/prefix under ComfyUI output")
    parser.add_argument("--width", type=int, default=1024, help="generated image width")
    parser.add_argument("--height", type=int, default=1024, help="generated image height")
    parser.add_argument("--steps", type=int, default=8, help="sampling steps")
    parser.add_argument("--seed-base", type=int, default=700000, help="seed for item 0; item index is added")
    parser.add_argument("--warmup", type=int, default=3, help="warm-up images excluded from results")
    parser.add_argument("--limit", type=int, default=0, help="maximum prompts to process; 0 means all")
    parser.add_argument("--poll-seconds", type=float, default=0.2, help="history polling interval")
    parser.add_argument("--retries", type=int, default=2, help="retries after the first attempt")
    parser.add_argument(
        "--require-device",
        default="",
        help="refuse to run unless the local ComfyUI device name contains this text",
    )
    parser.add_argument("--prompt-node", default="", metavar="ID", help="node whose text input receives each prompt; auto-detected when omitted")
    parser.add_argument("--size-node", default="", metavar="ID", help="node whose width/height inputs are updated; auto-detected when omitted")
    parser.add_argument("--sampler-node", default="", metavar="ID", help="node whose seed/steps inputs are updated; auto-detected when omitted")
    parser.add_argument("--save-node", default="", metavar="ID", help="node whose filename_prefix is updated; auto-detected when omitted")
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
        nodes = resolve_workflow_nodes(base, args)
    except ValueError as exc:
        parser.exit(2, f"ERROR: Invalid workflow: {exc}\n")

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
            workflow = configure(base, nodes, prompts[n % len(prompts)], n, args, "benchmark_warmup/warmup")
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
                workflow = configure(base, nodes, prompt, index, args, prefix)
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
