#!/usr/bin/env python3
"""Verify Intel Arc Pro B70 compute, VRAM, Above 4G Decoding, and ReBAR.

This Windows diagnostic uses only the standard library for its hardware checks.
It asks the Intel Graphics Control Library (IGCL) for the driver's explicit
Resizable BAR status. Raw Plug and Play Configuration Manager resources are
used for the separate Above 4G Decoding inference and diagnostic details only.

Use --compute from the ComfyUI virtual environment to run a real torch.xpu
matrix multiplication on the B70. If ComfyUI is already running, the script
also checks its /system_stats endpoint.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


FOUR_GIB = 4 * 1024**3
DISPLAY_CLASS_GUID = "{4d36e968-e325-11ce-bfc1-08002be10318}"
CR_SUCCESS = 0
ALLOC_LOG_CONF = 2
RES_TYPE_MEM = 1
RES_TYPE_MEM_LARGE = 7
DN_STARTED = 0x00000008


@dataclass
class GpuInfo:
    instance_id: str
    name: str
    vram_bytes: int | None
    memory_ranges: list[tuple[int, int]]
    pci_bdf: tuple[int, int, int] | None
    control_api_path: str | None


class CtlApplicationId(ctypes.Structure):
    _fields_ = [
        ("data1", ctypes.c_uint32),
        ("data2", ctypes.c_uint16),
        ("data3", ctypes.c_uint16),
        ("data4", ctypes.c_uint8 * 8),
    ]


class CtlInitArgs(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint32),
        ("version", ctypes.c_uint8),
        ("app_version", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("supported_version", ctypes.c_uint32),
        ("application_uid", CtlApplicationId),
    ]


class CtlPciAddress(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint32),
        ("version", ctypes.c_uint8),
        ("domain", ctypes.c_uint32),
        ("bus", ctypes.c_uint32),
        ("device", ctypes.c_uint32),
        ("function", ctypes.c_uint32),
    ]


class CtlPciSpeed(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint32),
        ("version", ctypes.c_uint8),
        ("generation", ctypes.c_int32),
        ("width", ctypes.c_int32),
        ("max_bandwidth", ctypes.c_int64),
    ]


class CtlPciProperties(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint32),
        ("version", ctypes.c_uint8),
        ("address", CtlPciAddress),
        ("max_speed", CtlPciSpeed),
        ("rebar_supported", ctypes.c_bool),
        ("rebar_enabled", ctypes.c_bool),
    ]


def format_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            precision = 0 if unit in ("B", "KiB", "MiB") else 2
            return f"{amount:.{precision}f} {unit}"
        amount /= 1024
    return f"{value} B"


def name_words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.casefold())


def name_matches(value: str, expected: str) -> bool:
    words = name_words(value)
    return all(word in words for word in name_words(expected))


def clean_device_name(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    # Registry names can be indirect strings such as
    # "@oem20.inf,%device%;Intel(R) Arc(TM) Pro B70 Graphics".
    return value.rsplit(";", 1)[-1].strip("{} ") or fallback


def windows_firmware_type() -> str:
    firmware_type = ctypes.c_uint(0)
    get_firmware_type = ctypes.windll.kernel32.GetFirmwareType
    get_firmware_type.argtypes = [ctypes.POINTER(ctypes.c_uint)]
    get_firmware_type.restype = ctypes.c_int
    if not get_firmware_type(ctypes.byref(firmware_type)):
        return "unknown"
    return {1: "bios", 2: "uefi"}.get(firmware_type.value, "unknown")


class ConfigurationManager:
    """Small ctypes binding for the CfgMgr32 calls needed by this check."""

    def __init__(self) -> None:
        self.dll = ctypes.WinDLL("CfgMgr32.dll")
        handle = ctypes.c_void_p
        ulong = ctypes.c_ulong

        self.locate = self.dll.CM_Locate_DevNodeW
        self.locate.argtypes = [ctypes.POINTER(ulong), ctypes.c_wchar_p, ulong]
        self.locate.restype = ulong

        self.get_status = self.dll.CM_Get_DevNode_Status
        self.get_status.argtypes = [ctypes.POINTER(ulong), ctypes.POINTER(ulong), ulong, ulong]
        self.get_status.restype = ulong

        self.first_log_conf = self.dll.CM_Get_First_Log_Conf
        self.first_log_conf.argtypes = [ctypes.POINTER(handle), ulong, ulong]
        self.first_log_conf.restype = ulong

        self.next_resource = self.dll.CM_Get_Next_Res_Des
        self.next_resource.argtypes = [
            ctypes.POINTER(handle), handle, ulong, ctypes.POINTER(ulong), ulong
        ]
        self.next_resource.restype = ulong

        self.resource_size = self.dll.CM_Get_Res_Des_Data_Size
        self.resource_size.argtypes = [ctypes.POINTER(ulong), handle, ulong]
        self.resource_size.restype = ulong

        self.resource_data = self.dll.CM_Get_Res_Des_Data
        self.resource_data.argtypes = [handle, ctypes.c_void_p, ulong, ulong]
        self.resource_data.restype = ulong

        self.free_resource = self.dll.CM_Free_Res_Des_Handle
        self.free_resource.argtypes = [handle]
        self.free_resource.restype = ulong

        self.free_log_conf = self.dll.CM_Free_Log_Conf_Handle
        self.free_log_conf.argtypes = [handle]
        self.free_log_conf.restype = ulong

    def locate_started(self, instance_id: str) -> int | None:
        devinst = ctypes.c_ulong(0)
        if self.locate(ctypes.byref(devinst), instance_id, 0) != CR_SUCCESS:
            return None
        status, problem = ctypes.c_ulong(0), ctypes.c_ulong(0)
        if self.get_status(ctypes.byref(status), ctypes.byref(problem), devinst, 0) != CR_SUCCESS:
            return None
        return devinst.value if status.value & DN_STARTED else None

    def _raw_descriptors(self, log_conf: ctypes.c_void_p, resource_type: int) -> list[bytes]:
        descriptors: list[bytes] = []
        resource = ctypes.c_void_p()
        if self.next_resource(
            ctypes.byref(resource), log_conf, resource_type, None, 0
        ) != CR_SUCCESS:
            return descriptors

        while True:
            size = ctypes.c_ulong(0)
            if self.resource_size(ctypes.byref(size), resource, 0) == CR_SUCCESS and size.value:
                data = ctypes.create_string_buffer(size.value)
                if self.resource_data(resource, data, size.value, 0) == CR_SUCCESS:
                    descriptors.append(data.raw)

            next_resource = ctypes.c_void_p()
            result = self.next_resource(
                ctypes.byref(next_resource), resource, resource_type, None, 0
            )
            self.free_resource(resource)
            if result != CR_SUCCESS:
                break
            resource = next_resource
        return descriptors

    def memory_ranges(self, devinst: int) -> tuple[list[tuple[int, int]], str | None]:
        log_conf = ctypes.c_void_p()
        result = self.first_log_conf(ctypes.byref(log_conf), devinst, ALLOC_LOG_CONF)
        if result != CR_SUCCESS:
            return [], f"CM_Get_First_Log_Conf failed (CONFIGRET 0x{result:08X})"

        ranges: list[tuple[int, int]] = []
        try:
            # Both headers are packed as Count/Type followed by 64-bit
            # Alloc_Base and Alloc_End values.
            for data in self._raw_descriptors(log_conf, RES_TYPE_MEM):
                if len(data) >= 24:
                    start, end = struct.unpack_from("<QQ", data, 8)
                    if end >= start:
                        ranges.append((start, end))

            # MEM_LARGE_RESOURCE is packed and stores 64-bit MLD_Alloc_Base/End
            # at offsets 8 and 16. Large B70 apertures can appear here, but
            # their size alone does not prove the current ReBAR state.
            for data in self._raw_descriptors(log_conf, RES_TYPE_MEM_LARGE):
                if len(data) >= 24:
                    start, end = struct.unpack_from("<QQ", data, 8)
                    if end >= start:
                        ranges.append((start, end))
        finally:
            self.free_log_conf(log_conf)
        return ranges, None


def parse_pci_location(value: object) -> tuple[int, int, int] | None:
    if not isinstance(value, str):
        return None
    # LocationInformation is commonly an indirect localized resource followed
    # by the stable numeric tuple "(bus,device,function)".
    match = re.search(r"\((\d+)\s*,\s*(\d+)\s*,\s*(\d+)\)\s*$", value)
    return tuple(map(int, match.groups())) if match else None


def registry_display_devices() -> dict[
    str, tuple[str, int | None, tuple[int, int, int] | None, str | None]
]:
    import winreg

    devices: dict[
        str, tuple[str, int | None, tuple[int, int, int] | None, str | None]
    ] = {}
    enum_path = r"SYSTEM\CurrentControlSet\Enum\PCI"
    class_path = r"SYSTEM\CurrentControlSet\Control\Class"

    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, enum_path) as pci_root:
        for device_index in range(winreg.QueryInfoKey(pci_root)[0]):
            device_key_name = winreg.EnumKey(pci_root, device_index)
            with winreg.OpenKey(pci_root, device_key_name) as device_key:
                for instance_index in range(winreg.QueryInfoKey(device_key)[0]):
                    instance_key_name = winreg.EnumKey(device_key, instance_index)
                    try:
                        with winreg.OpenKey(device_key, instance_key_name) as instance_key:
                            class_guid = winreg.QueryValueEx(instance_key, "ClassGUID")[0]
                            if str(class_guid).casefold() != DISPLAY_CLASS_GUID.casefold():
                                continue
                            driver_key_name = winreg.QueryValueEx(instance_key, "Driver")[0]
                            fallback_name = clean_device_name(
                                winreg.QueryValueEx(instance_key, "DeviceDesc")[0], device_key_name
                            )
                            try:
                                pci_bdf = parse_pci_location(
                                    winreg.QueryValueEx(instance_key, "LocationInformation")[0]
                                )
                            except FileNotFoundError:
                                pci_bdf = None
                    except (FileNotFoundError, OSError):
                        continue

                    name = fallback_name
                    vram_bytes: int | None = None
                    control_api_path: str | None = None
                    try:
                        with winreg.OpenKey(
                            winreg.HKEY_LOCAL_MACHINE, f"{class_path}\\{driver_key_name}"
                        ) as driver_key:
                            try:
                                name = clean_device_name(
                                    winreg.QueryValueEx(driver_key, "DriverDesc")[0], fallback_name
                                )
                            except FileNotFoundError:
                                pass
                            try:
                                api_value = winreg.QueryValueEx(driver_key, "ControlApiPath")[0]
                                if isinstance(api_value, str) and api_value:
                                    control_api_path = os.path.expandvars(api_value)
                            except FileNotFoundError:
                                pass
                            try:
                                memory_value = winreg.QueryValueEx(
                                    driver_key, "HardwareInformation.qwMemorySize"
                                )[0]
                                if isinstance(memory_value, int) and memory_value > 0:
                                    vram_bytes = memory_value
                            except FileNotFoundError:
                                try:
                                    memory_value = winreg.QueryValueEx(
                                        driver_key, "HardwareInformation.MemorySize"
                                    )[0]
                                    if isinstance(memory_value, int) and memory_value > 0:
                                        vram_bytes = memory_value
                                    elif isinstance(memory_value, bytes) and len(memory_value) >= 4:
                                        vram_bytes = struct.unpack_from("<I", memory_value)[0]
                                except FileNotFoundError:
                                    pass
                    except OSError:
                        pass

                    instance_id = f"PCI\\{device_key_name}\\{instance_key_name}".upper()
                    devices[instance_id] = (name, vram_bytes, pci_bdf, control_api_path)
    return devices


def query_igcl_rebar(
    control_api_path: str, expected_bdf: tuple[int, int, int] | None
) -> tuple[bool | None, bool | None, str | None]:
    """Return (supported, enabled, error) from Intel's driver-shipped IGCL."""
    if not os.path.isfile(control_api_path):
        return None, None, f"Intel Control Library was not found at {control_api_path}"

    if ctypes.sizeof(CtlInitArgs) != 36 or ctypes.sizeof(CtlPciProperties) != 64:
        return None, None, "unexpected IGCL structure layout in this Python build"

    dll_directory = None
    try:
        if hasattr(os, "add_dll_directory"):
            dll_directory = os.add_dll_directory(os.path.dirname(control_api_path))
        igcl = ctypes.CDLL(control_api_path)

        igcl.ctlInit.argtypes = [ctypes.POINTER(CtlInitArgs), ctypes.POINTER(ctypes.c_void_p)]
        igcl.ctlInit.restype = ctypes.c_uint32
        igcl.ctlClose.argtypes = [ctypes.c_void_p]
        igcl.ctlClose.restype = ctypes.c_uint32
        igcl.ctlEnumerateDevices.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        igcl.ctlEnumerateDevices.restype = ctypes.c_uint32
        igcl.ctlPciGetProperties.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(CtlPciProperties),
        ]
        igcl.ctlPciGetProperties.restype = ctypes.c_uint32

        init_args = CtlInitArgs()
        init_args.size = ctypes.sizeof(init_args)
        init_args.version = 0
        init_args.app_version = (1 << 16) | 1  # Official IGCL 1.1 ABI.
        init_args.flags = 1  # CTL_INIT_FLAG_USE_LEVEL_ZERO.
        api_handle = ctypes.c_void_p()
        result = igcl.ctlInit(ctypes.byref(init_args), ctypes.byref(api_handle))
        if result != CR_SUCCESS or not api_handle:
            return None, None, f"Intel Control Library initialization failed (0x{result:08X})"

        try:
            count = ctypes.c_uint32(0)
            result = igcl.ctlEnumerateDevices(api_handle, ctypes.byref(count), None)
            if result != CR_SUCCESS or not count.value:
                return None, None, f"Intel adapter enumeration failed (0x{result:08X})"

            handles = (ctypes.c_void_p * count.value)()
            result = igcl.ctlEnumerateDevices(api_handle, ctypes.byref(count), handles)
            if result != CR_SUCCESS:
                return None, None, f"Intel adapter enumeration failed (0x{result:08X})"

            statuses: list[tuple[tuple[int, int, int], bool, bool]] = []
            pci_errors: list[int] = []
            for handle in handles[: count.value]:
                properties = CtlPciProperties()
                properties.size = ctypes.sizeof(properties)
                properties.version = 0
                result = igcl.ctlPciGetProperties(handle, ctypes.byref(properties))
                if result != CR_SUCCESS:
                    pci_errors.append(result)
                    continue
                bdf = (
                    properties.address.bus,
                    properties.address.device,
                    properties.address.function,
                )
                statuses.append((bdf, properties.rebar_supported, properties.rebar_enabled))

            matched = next((status for status in statuses if status[0] == expected_bdf), None)
            if matched is None and expected_bdf is None and len(statuses) == 1:
                matched = statuses[0]
            if matched is None:
                detected = ", ".join(
                    f"{bus}:{device}.{function}"
                    for (bus, device, function), _, _ in statuses
                )
                expected = (
                    "unknown"
                    if expected_bdf is None
                    else f"{expected_bdf[0]}:{expected_bdf[1]}.{expected_bdf[2]}"
                )
                return None, None, (
                    f"Intel driver did not return PCI status for BDF {expected}; "
                    f"detected: {detected or 'none'}"
                    + (
                        "; ctlPciGetProperties errors: "
                        + ", ".join(f"0x{error:08X}" for error in pci_errors)
                        if pci_errors
                        else ""
                    )
                )
            return matched[1], matched[2], None
        finally:
            igcl.ctlClose(api_handle)
    except (AttributeError, OSError) as exc:
        return None, None, f"Intel Control Library query failed: {exc}"
    finally:
        if dll_directory is not None:
            dll_directory.close()


def active_gpus(name_filter: str) -> tuple[list[GpuInfo], list[str]]:
    warnings: list[str] = []
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        return [], ["64-bit Python is required to query native 64-bit PCI resources"]

    try:
        registry_devices = registry_display_devices()
        manager = ConfigurationManager()
    except OSError as exc:
        return [], [f"could not initialize the Windows hardware check: {exc}"]

    gpus: list[GpuInfo] = []
    for instance_id, (name, vram_bytes, pci_bdf, control_api_path) in registry_devices.items():
        devinst = manager.locate_started(instance_id)
        if devinst is None:
            continue
        ranges, error = manager.memory_ranges(devinst)
        if error:
            warnings.append(f"{name}: {error}")
        gpus.append(GpuInfo(instance_id, name, vram_bytes, ranges, pci_bdf, control_api_path))

    if name_filter:
        filtered = [gpu for gpu in gpus if name_matches(gpu.name, name_filter)]
        if filtered:
            return filtered, warnings
        warnings.append(f"no active GPU matched {name_filter!r}; showing all active display adapters")
    return gpus, warnings


def print_result(label: str, status: str, message: str) -> None:
    print(f"[{status:<4}] {label}: {message}")


def check_gpu(gpu: GpuInfo, verbose: bool) -> bool:
    print(f"\nGPU: {gpu.name}")
    has_warning = False

    if gpu.vram_bytes is None:
        print_result("Dedicated VRAM", "INFO", "could not be determined")
    elif gpu.vram_bytes >= FOUR_GIB:
        print_result("Dedicated VRAM", "PASS", f"{format_bytes(gpu.vram_bytes)} (at least 4 GiB)")
    else:
        print_result("Dedicated VRAM", "WARN", f"{format_bytes(gpu.vram_bytes)}; 4 GiB or more is recommended")
        has_warning = True

    ranges = sorted(gpu.memory_ranges, key=lambda item: item[1] - item[0], reverse=True)
    if verbose:
        for start, end in ranges:
            print(f"       PCI memory 0x{start:016X}-0x{end:016X} ({format_bytes(end - start + 1)})")
    above_4g = any(start >= FOUR_GIB or end >= FOUR_GIB for start, end in ranges)

    if above_4g:
        print_result("Above 4G Decoding", "PASS", "GPU PCI memory is mapped above the 4 GiB boundary")
    else:
        print_result("Above 4G Decoding", "WARN", "no GPU PCI memory mapping was found above 4 GiB")
        has_warning = True

    rebar_supported: bool | None = None
    rebar_enabled: bool | None = None
    rebar_error: str | None = None
    if gpu.control_api_path:
        rebar_supported, rebar_enabled, rebar_error = query_igcl_rebar(
            gpu.control_api_path, gpu.pci_bdf
        )
    else:
        rebar_error = "Intel driver did not publish its ControlApiPath"

    if rebar_enabled:
        print_result(
            "Resizable BAR (effective)",
            "PASS",
            "enabled according to Intel Graphics Control Library",
        )
    elif rebar_enabled is False:
        print_result(
            "Resizable BAR (effective)",
            "WARN",
            "disabled according to Intel Graphics Control Library"
            if rebar_supported
            else "unsupported and disabled according to Intel Graphics Control Library",
        )
        has_warning = True
    else:
        largest_bar = ranges[0][1] - ranges[0][0] + 1 if ranges else 0
        aperture_note = (
            f"; raw aperture {format_bytes(largest_bar)} is not proof of ReBAR state"
            if largest_bar
            else ""
        )
        print_result(
            "Resizable BAR (effective)",
            "INFO",
            f"could not be confirmed ({rebar_error}){aperture_note}",
        )
        has_warning = True
    return has_warning


def check_comfyui_server(server: str, expected_gpu: str) -> bool | None:
    try:
        with urllib.request.urlopen(f"{server.rstrip('/')}/system_stats", timeout=3) as response:
            stats = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        print_result("ComfyUI device", "INFO", f"server is not reachable at {server}; check again after launch")
        return None

    names = [str(device.get("name", "")) for device in stats.get("devices", [])]
    matched = next((name for name in names if name_matches(name, expected_gpu)), "")
    if matched:
        print_result("ComfyUI device", "PASS", matched)
        return True
    detected = ", ".join(names) if names else "no compute devices"
    print_result("ComfyUI device", "WARN", f"B70 not reported; detected: {detected}")
    return False


def run_xpu_compute(expected_gpu: str, matrix_size: int) -> bool:
    print("\nB70 torch.xpu compute test:")
    try:
        import torch
    except Exception as exc:
        print_result("PyTorch XPU", "WARN", f"torch could not be imported: {exc}")
        print("       Run this check with .\\.venv\\Scripts\\python.exe after setup.")
        return False

    if not hasattr(torch, "xpu") or not torch.xpu.is_available():
        print_result("PyTorch XPU", "WARN", "XPU is unavailable; the workload would not run on B70")
        return False

    device_count = torch.xpu.device_count()
    device_index = next(
        (index for index in range(device_count) if name_matches(torch.xpu.get_device_name(index), expected_gpu)),
        None,
    )
    if device_index is None:
        names = [torch.xpu.get_device_name(index) for index in range(device_count)]
        print_result("PyTorch XPU", "WARN", f"B70 not found; detected: {', '.join(names) or 'none'}")
        return False

    device = torch.device(f"xpu:{device_index}")
    device_name = torch.xpu.get_device_name(device_index)
    torch.xpu.reset_peak_memory_stats(device_index)
    try:
        with torch.inference_mode():
            a = torch.randn((matrix_size, matrix_size), device=device)
            b = torch.randn((matrix_size, matrix_size), device=device)
            _ = a @ b
            torch.xpu.synchronize(device_index)
            started = time.perf_counter()
            result = a @ b
            torch.xpu.synchronize(device_index)
            elapsed = time.perf_counter() - started
            peak_vram = torch.xpu.max_memory_allocated(device_index)
    except Exception as exc:
        print_result("B70 computation", "WARN", f"XPU operation failed: {exc}")
        return False

    if result.device.type == "xpu" and result.device.index == device_index and peak_vram > 0:
        print_result("PyTorch XPU", "PASS", f"{torch.__version__}; selected xpu:{device_index} {device_name}")
        print_result(
            "B70 computation",
            "PASS",
            f"{matrix_size}x{matrix_size} matrix multiplication completed in {elapsed * 1000:.1f} ms; "
            f"peak allocated VRAM {format_bytes(peak_vram)}",
        )
        return True
    print_result("B70 computation", "WARN", "result tensor or VRAM accounting did not confirm XPU execution")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu", default="Intel Arc Pro B70", help="GPU name filter (default: %(default)s)")
    parser.add_argument("--server", default="http://127.0.0.1:8188", help="ComfyUI server URL")
    parser.add_argument("--compute", action="store_true", help="run a real torch.xpu matrix multiplication")
    parser.add_argument("--matrix-size", type=int, default=2048, help="matrix size used by --compute")
    parser.add_argument("--verbose", action="store_true", help="show raw PCI memory ranges")
    args = parser.parse_args()

    print("ComfyUI Intel Arc Pro B70 readiness check")
    print("BIOS warnings are advisory and never prevent ComfyUI from starting.\n")

    if os.name != "nt":
        print_result("Platform", "INFO", "this hardware-resource checker currently supports Windows only")
        return 0
    if args.matrix_size < 256:
        parser.error("--matrix-size must be at least 256")

    firmware = windows_firmware_type()
    if firmware == "uefi":
        print_result("Boot mode", "PASS", "UEFI")
    elif firmware == "bios":
        print_result("Boot mode", "WARN", "Legacy BIOS; UEFI is recommended for Resizable BAR")
    else:
        print_result("Boot mode", "INFO", "could not be determined")

    gpus, diagnostic_warnings = active_gpus(args.gpu)
    for warning in diagnostic_warnings:
        print_result("Diagnostic", "INFO", warning)

    bios_warning = firmware == "bios"
    if not gpus:
        print_result("GPU", "WARN", f"no active display adapter matched {args.gpu!r}")
        bios_warning = True
    else:
        for gpu in gpus:
            bios_warning = check_gpu(gpu, args.verbose) or bios_warning

    server_result = check_comfyui_server(args.server, args.gpu)
    compute_result = run_xpu_compute(args.gpu, args.matrix_size) if args.compute else None

    print("\nResult:")
    if compute_result is True:
        print("  Confirmed: a real torch.xpu operation ran on the Intel Arc Pro B70.")
    elif args.compute:
        print("  Not confirmed: the B70 torch.xpu computation failed or could not be verified.")
    else:
        print("  Run again with --compute from the ComfyUI .venv to prove B70 execution.")

    if server_result is True:
        print("  Confirmed: the running ComfyUI server reports the B70 as its compute device.")
    if bios_warning:
        print("  One or more recommended BIOS/GPU settings could not be confirmed.")
        print("  ComfyUI can still run, but generation may be substantially slower.")
        print("  If available, enable Above 4G Decoding and Re-Size BAR in BIOS/UEFI.")
    else:
        print("  Above 4G Decoding and effective Resizable BAR are available to Windows.")
    print("  This reports the effective Windows state, not the literal BIOS-menu switch.")
    print("  Cold boot and compare Intel Graphics Software or Intel DSA if the result is unexpected.")

    # BIOS warnings are deliberately non-blocking. A requested compute test,
    # however, returns failure when it cannot prove B70 execution.
    return 1 if args.compute and compute_result is not True else 0


if __name__ == "__main__":
    raise SystemExit(main())
