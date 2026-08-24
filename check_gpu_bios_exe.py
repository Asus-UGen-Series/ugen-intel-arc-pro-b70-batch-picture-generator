#!/usr/bin/env python3
"""Check B70 readiness and use check_rebar.exe for Resizable BAR status.

Place check_rebar.exe beside this file. The executable's documented exit codes
are authoritative for this wrapper: 0 means enabled, 1 means disabled, and 2
means the check failed. BIOS warnings remain advisory and never block ComfyUI.

Run with --compute from the ComfyUI virtual environment to prove that a real
torch.xpu operation executes on the Intel Arc Pro B70.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

import check_gpu_bios as common


def check_gpu_without_rebar(gpu: common.GpuInfo, verbose: bool) -> bool:
    """Print B70, VRAM, and Above 4G results; ReBAR is checked separately."""
    print(f"\nGPU: {gpu.name}")
    has_warning = False

    if gpu.vram_bytes is None:
        common.print_result("Dedicated VRAM", "INFO", "could not be determined")
    elif gpu.vram_bytes >= common.FOUR_GIB:
        common.print_result(
            "Dedicated VRAM",
            "PASS",
            f"{common.format_bytes(gpu.vram_bytes)} (at least 4 GiB)",
        )
    else:
        common.print_result(
            "Dedicated VRAM",
            "WARN",
            f"{common.format_bytes(gpu.vram_bytes)}; 4 GiB or more is recommended",
        )
        has_warning = True

    ranges = sorted(gpu.memory_ranges, key=lambda item: item[1] - item[0], reverse=True)
    if verbose:
        for start, end in ranges:
            print(
                f"       PCI memory 0x{start:016X}-0x{end:016X} "
                f"({common.format_bytes(end - start + 1)})"
            )

    above_4g = any(
        start >= common.FOUR_GIB or end >= common.FOUR_GIB for start, end in ranges
    )
    if above_4g:
        common.print_result(
            "Above 4G Decoding",
            "PASS",
            "GPU PCI memory is mapped above the 4 GiB boundary",
        )
    else:
        common.print_result(
            "Above 4G Decoding",
            "WARN",
            "no GPU PCI memory mapping was found above 4 GiB",
        )
        has_warning = True
    return has_warning


def run_rebar_exe(executable: Path, timeout: float) -> tuple[bool | None, str]:
    """Run check_rebar.exe without a shell and interpret its documented codes."""
    try:
        executable = executable.expanduser().resolve(strict=True)
    except OSError as exc:
        return None, f"check_rebar.exe was not found: {exc}"
    if not executable.is_file():
        return None, f"not a file: {executable}"

    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [str(executable)],
            cwd=str(executable.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
            creationflags=creation_flags,
        )
    except subprocess.TimeoutExpired:
        return None, f"timed out after {timeout:g} seconds"
    except OSError as exc:
        return None, f"could not start {executable.name}: {exc}"

    output = " ".join(
        line.strip()
        for stream in (result.stdout, result.stderr)
        for line in stream.splitlines()
        if line.strip()
    )
    detail = output or "no output"
    if result.returncode == 0:
        return True, f"enabled ({executable.name} exit code 0; {detail})"
    if result.returncode == 1:
        return False, f"disabled ({executable.name} exit code 1; {detail})"
    return None, f"check failed (exit code {result.returncode}; {detail})"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu", default="Intel Arc Pro B70", help="GPU name filter")
    parser.add_argument("--server", default="http://127.0.0.1:8188", help="ComfyUI server URL")
    parser.add_argument("--compute", action="store_true", help="run a real torch.xpu operation")
    parser.add_argument("--matrix-size", type=int, default=2048, help="matrix size for --compute")
    parser.add_argument("--verbose", action="store_true", help="show raw PCI memory ranges")
    parser.add_argument(
        "--rebar-exe",
        type=Path,
        default=Path(__file__).resolve().with_name("check_rebar.exe"),
        help="path to check_rebar.exe (default: beside this Python file)",
    )
    parser.add_argument("--rebar-timeout", type=float, default=15, help="exe timeout in seconds")
    args = parser.parse_args()

    print("ComfyUI Intel Arc Pro B70 readiness check (check_rebar.exe edition)")
    print("BIOS warnings are advisory and never prevent ComfyUI from starting.\n")

    if os.name != "nt":
        common.print_result("Platform", "INFO", "this checker supports Windows only")
        return 0
    if args.matrix_size < 256:
        parser.error("--matrix-size must be at least 256")
    if args.rebar_timeout <= 0:
        parser.error("--rebar-timeout must be greater than zero")

    firmware = common.windows_firmware_type()
    if firmware == "uefi":
        common.print_result("Boot mode", "PASS", "UEFI")
    elif firmware == "bios":
        common.print_result("Boot mode", "WARN", "Legacy BIOS; UEFI is recommended")
    else:
        common.print_result("Boot mode", "INFO", "could not be determined")

    gpus, diagnostic_warnings = common.active_gpus(args.gpu)
    for warning in diagnostic_warnings:
        common.print_result("Diagnostic", "INFO", warning)

    bios_warning = firmware == "bios"
    if not gpus:
        common.print_result("GPU", "WARN", f"no active display adapter matched {args.gpu!r}")
        bios_warning = True
    else:
        for gpu in gpus:
            bios_warning = check_gpu_without_rebar(gpu, args.verbose) or bios_warning

    rebar_enabled, rebar_message = run_rebar_exe(args.rebar_exe, args.rebar_timeout)
    if rebar_enabled is True:
        common.print_result("Resizable BAR", "PASS", rebar_message)
    elif rebar_enabled is False:
        common.print_result("Resizable BAR", "WARN", rebar_message)
        bios_warning = True
    else:
        common.print_result("Resizable BAR", "INFO", rebar_message)
        bios_warning = True

    server_result = common.check_comfyui_server(args.server, args.gpu)
    compute_result = (
        common.run_xpu_compute(args.gpu, args.matrix_size) if args.compute else None
    )

    print("\nResult:")
    if compute_result is True:
        print("  Confirmed: a real torch.xpu operation ran on the Intel Arc Pro B70.")
    elif args.compute:
        print("  Not confirmed: B70 torch.xpu computation failed or could not be verified.")
    else:
        print("  Run again with --compute from the ComfyUI .venv to prove B70 execution.")
    if server_result is True:
        print("  Confirmed: the running ComfyUI server reports B70 as its compute device.")
    if bios_warning:
        print("  One or more recommended BIOS/GPU settings could not be confirmed.")
        print("  ComfyUI can still run, but generation may be substantially slower.")
    else:
        print("  Above 4G Decoding and Resizable BAR appear available to Windows.")
    print("  Resizable BAR above is exactly the check_rebar.exe exit-code result.")

    return 1 if args.compute and compute_result is not True else 0


if __name__ == "__main__":
    raise SystemExit(main())
