"""USB debug probe detection via lsusb and sysfs fallback."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ProbeInfo:
    name: str
    probe_type: str  # "stlink", "jlink", "cmsis-dap"
    vid: int = 0
    pid: int = 0
    serial: str = ""
    bus: str = ""


KNOWN_PROBES: dict[tuple[int, int], tuple[str, str]] = {
    # ST-Link variants
    (0x0483, 0x3748): ("ST-Link V2", "stlink"),
    (0x0483, 0x374B): ("ST-Link V2-1", "stlink"),
    (0x0483, 0x374D): ("ST-Link V3-MINI", "stlink"),
    (0x0483, 0x374E): ("ST-Link V3", "stlink"),
    (0x0483, 0x374F): ("ST-Link V3-SET", "stlink"),
    # J-Link
    (0x1366, 0x0101): ("J-Link", "jlink"),
    (0x1366, 0x0105): ("J-Link OB", "jlink"),
    (0x1366, 0x1015): ("J-Link", "jlink"),
    (0x1366, 0x1020): ("J-Link", "jlink"),
    # CMSIS-DAP / DAPLink
    (0x0D28, 0x0204): ("CMSIS-DAP (DAPLink)", "cmsis-dap"),
    # Black Magic Probe
    (0x1D50, 0x6018): ("Black Magic Probe", "bmp"),
    # Raspberry Pi Debug Probe (Picoprobe / debugprobe)
    (0x2E8A, 0x000C): ("Raspberry Pi Debug Probe", "cmsis-dap"),
    (0x2E8A, 0x0004): ("Picoprobe (RP2040)", "cmsis-dap"),
    # NXP MCU-Link / LPC-Link2
    (0x1FC9, 0x0143): ("NXP MCU-Link", "cmsis-dap"),
    (0x1FC9, 0x0090): ("NXP LPC-Link2", "cmsis-dap"),
    # TI XDS110
    (0x0451, 0xBEF3): ("TI XDS110", "xds110"),
    (0x0451, 0xBEF4): ("TI XDS110 with MSP432", "xds110"),
    # Microchip Atmel-ICE
    (0x03EB, 0x2141): ("Atmel-ICE", "cmsis-dap"),
    # Microchip EDBG (onboard Xplained boards)
    (0x03EB, 0x2111): ("Atmel EDBG", "cmsis-dap"),
    # WCH-Link (for CH32 series)
    (0x1A86, 0x8010): ("WCH-Link", "wch-link"),
    (0x1A86, 0x8012): ("WCH-LinkE", "wch-link"),
    # Sipeed RV-Debugger (for RISC-V targets)
    (0x0403, 0x6010): ("FTDI Dual (Sipeed/generic)", "ftdi"),
    # ESP-Prog (Espressif JTAG adapter)
    (0x0403, 0x6014): ("FTDI Quad (ESP-Prog)", "ftdi"),
    # ESP32-S3/C3/C6 built-in USB-JTAG
    (0x303A, 0x1001): ("ESP32 USB-JTAG", "esp-usb-jtag"),
    # DAP-Link (generic CMSIS-DAP implementations)
    (0xC251, 0xF001): ("Keil ULINK2", "cmsis-dap"),
    (0xC251, 0x2750): ("Keil ULINKplus", "cmsis-dap"),
}


async def detect_probes() -> list[ProbeInfo]:
    """Detect connected debug probes."""
    probes = await _detect_via_lsusb()
    if not probes:
        probes = _detect_via_sysfs()
    return probes


async def _detect_via_lsusb() -> list[ProbeInfo]:
    try:
        proc = await asyncio.create_subprocess_exec(
            "lsusb",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
    except (FileNotFoundError, asyncio.TimeoutError):
        return []

    probes: list[ProbeInfo] = []
    for line in stdout.decode(errors="replace").splitlines():
        match = re.match(r"Bus (\d+) .* ID (\w+):(\w+)", line)
        if not match:
            continue
        bus = match.group(1)
        vid = int(match.group(2), 16)
        pid = int(match.group(3), 16)

        key = (vid, pid)
        if key in KNOWN_PROBES:
            name, ptype = KNOWN_PROBES[key]
            probes.append(
                ProbeInfo(name=name, probe_type=ptype, vid=vid, pid=pid, bus=bus)
            )

    return probes


def _detect_via_sysfs() -> list[ProbeInfo]:
    """Fallback: read USB device info from /sys/bus/usb/devices."""
    probes: list[ProbeInfo] = []
    usb_base = Path("/sys/bus/usb/devices")
    if not usb_base.is_dir():
        return probes

    for dev_dir in usb_base.iterdir():
        vid_file = dev_dir / "idVendor"
        pid_file = dev_dir / "idProduct"
        if not vid_file.is_file() or not pid_file.is_file():
            continue

        try:
            vid = int(vid_file.read_text().strip(), 16)
            pid = int(pid_file.read_text().strip(), 16)
        except (ValueError, OSError):
            continue

        key = (vid, pid)
        if key in KNOWN_PROBES:
            name, ptype = KNOWN_PROBES[key]
            serial = ""
            serial_file = dev_dir / "serial"
            if serial_file.is_file():
                try:
                    serial = serial_file.read_text().strip()
                except OSError:
                    pass
            probes.append(
                ProbeInfo(
                    name=name,
                    probe_type=ptype,
                    vid=vid,
                    pid=pid,
                    serial=serial,
                    bus=dev_dir.name,
                )
            )

    return probes


async def detect_target_chip(probe: ProbeInfo) -> str | None:
    """Attempt to auto-detect the target chip via the probe's GDB server."""
    if probe.probe_type == "stlink":
        return await _detect_via_openocd(probe)
    if probe.probe_type == "jlink":
        return await _detect_via_jlink(probe)
    return None


async def _detect_via_openocd(probe: ProbeInfo) -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "openocd",
            "-f", "interface/stlink.cfg",
            "-c", "transport select hla_swd",
            "-c", "init",
            "-c", "targets",
            "-c", "shutdown",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        output = stderr.decode(errors="replace")
        # Look for target name in output
        match = re.search(r"target\s+\d+\s+\((\S+)\)", output)
        if match:
            return match.group(1)
        # Look for chip ID
        match = re.search(r"STM32(\w+)", output)
        if match:
            return f"STM32{match.group(1)}"
    except (FileNotFoundError, asyncio.TimeoutError):
        pass
    return None


async def _detect_via_jlink(probe: ProbeInfo) -> str | None:
    try:
        proc = await asyncio.create_subprocess_exec(
            "JLinkExe",
            "-CommandFile", "/dev/null",
            "-AutoConnect", "1",
            "-ExitOnError", "1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        output = stdout.decode(errors="replace")
        match = re.search(r"Device:\s+(\S+)", output)
        if match:
            return match.group(1)
    except (FileNotFoundError, asyncio.TimeoutError):
        pass
    return None
