"""FastMCP server exposing all JTAG debug tools to Claude Code."""

from __future__ import annotations

import struct
from contextlib import asynccontextmanager
from typing import Any

import json as _json

from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from mcp.types import TextContent

from .gdb.mi_connection import GDBError
from .gdb.session import DebugSession
from .gdb.types import GDBState
from .safety import (
    TOOL_TIERS,
    SafetyTier,
    check_gdb_raw_safety,
    require_confirmation,
)
from .session_store import SessionStore
from .svd.decoder import RegisterDecoder
from .svd.parser import SVDParser


# ── State ────────────────────────────────────────────────────────────

_sessions: dict[str, DebugSession] = {}
_svd_parser = SVDParser()
_svd_decoders: dict[str, RegisterDecoder] = {}
_svd_paths: dict[str, str] = {}
_saleae_connections: dict[str, Any] = {}
_saleae_coordinators: dict[str, Any] = {}
_store = SessionStore()


def _tool_result(text: str, data: Any = None) -> str:
    """Return formatted text for display."""
    return text


def _get_session(session_id: str) -> DebugSession:
    session = _sessions.get(session_id)
    if session is None:
        raise ValueError(
            f"No session '{session_id}'. Call session_connect first. "
            f"Active sessions: {list(_sessions.keys()) or 'none'}"
        )
    return session


def _get_decoder(session_id: str) -> RegisterDecoder | None:
    return _svd_decoders.get(session_id)


def _format_hex_dump(data: bytes, base_addr: int) -> str:
    """Format bytes as hex dump with ASCII sidebar."""
    lines = []
    for offset in range(0, len(data), 16):
        chunk = data[offset : offset + 16]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"0x{base_addr + offset:08x}  {hex_part:<48s}  |{ascii_part}|")
    return "\n".join(lines)


# ── ANSI Color (256-color safe for Claude Code) ─────────────────────

# ── Theme-aware 256-color palette ────────────────────────────────────

def _get_claude_theme() -> str:
    from pathlib import Path
    try:
        import json as _j
        return _j.loads((Path.home() / ".claude.json").read_text()).get("theme", "dark")
    except Exception:
        return "dark"

_THEME = _get_claude_theme()

# Basic 16-color ANSI — renders in Claude Code tool output
_PALETTES = {
    "dark": {
        "rst":       "\x1b[0m",
        "bold":      "\x1b[1m",
        "dim":       "\x1b[2m",
        "reg_val":   "\x1b[32m",          # green
        "addr":      "\x1b[36m",          # cyan
        "func":      "\x1b[33m",          # yellow
        "pc_line":   "\x1b[1;37m",        # bold white
        "section":   "\x1b[2m",           # dim
        "src":       "",                   # default
        "warn":      "\x1b[33m",          # yellow
        "hit":       "\x1b[1;31m",        # bold red
        "flag_set":  "\x1b[1;32m",        # bold green
        "flag_clr":  "\x1b[2m",           # dim
    },
    "light": {
        "rst":       "\x1b[0m",
        "bold":      "\x1b[1m",
        "dim":       "\x1b[2m",
        "reg_val":   "\x1b[32m",
        "addr":      "\x1b[34m",
        "func":      "\x1b[33m",
        "pc_line":   "\x1b[1;30m",
        "section":   "\x1b[2m",
        "src":       "",
        "warn":      "\x1b[33m",
        "hit":       "\x1b[1;31m",
        "flag_set":  "\x1b[1;32m",
        "flag_clr":  "\x1b[2m",
    },
}
_PALETTES["dark-daltonized"] = _PALETTES["dark"]
_PALETTES["light-daltonized"] = _PALETTES["light"]
_C = _PALETTES.get(_THEME, _PALETTES["dark"])

_RST = _C["rst"]
_BOLD = _C["bold"]
_DIM = _C["dim"]
_REG_VAL = _C["reg_val"]
_ADDR = _C["addr"]
_FUNC = _C["func"]
_PC_LINE = _C["pc_line"]
_SECTION = _C["section"]
_SRC = _C["src"]
_WARN = _C["warn"]
_HIT = _C["hit"]
_FLAG_SET = _C["flag_set"]
_FLAG_CLR = _C["flag_clr"]


def _vpad(s: str, width: int) -> str:
    """Pad string to visible width, ignoring ANSI escape codes."""
    import re
    visible_len = len(re.sub(r'\x1b\[[0-9;]*m', '', s))
    return s + " " * max(0, width - visible_len)


# ── Output Formatters ───────────────────────────────────────────────

def _format_stop_line(stop: dict[str, Any]) -> str:
    """Format a stop event as a concise one-liner."""
    reason = stop.get("reason", "unknown")
    frame = stop.get("frame", {})
    func = frame.get("function", "??")
    addr = frame.get("address", "")
    file = frame.get("file", "")
    line = frame.get("line", "")
    loc = f" ({file}:{line})" if file and line else ""
    bp = stop.get("breakpoint_id", "")
    bp_str = f"  [breakpoint #{bp}]" if bp else ""
    sig = stop.get("signal_name", "")
    sig_str = f" ({sig})" if sig and reason == "signal-received" else ""
    return f"Stopped: {reason}{sig_str} at {_FUNC}{func}{_RST}{loc} [{_ADDR}{addr}{_RST}]{bp_str}"


def _format_registers(regs: dict[str, str]) -> str:
    """Format registers as aligned columns."""
    preferred = [
        "r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7",
        "r8", "r9", "r10", "r11", "r12",
        "sp", "lr", "pc", "xpsr", "cpsr", "fpscr",
    ]
    ordered = []
    for name in preferred:
        if name in regs:
            ordered.append((name, regs[name]))
    for name, val in regs.items():
        if name not in preferred:
            ordered.append((name, val))

    lines = []
    row = []
    for name, val in ordered:
        entry = f"  {name:<5s}= {_REG_VAL}{val}{_RST}"
        row.append(_vpad(entry, 18))
        if len(row) == 4:
            lines.append("  ".join(row))
            row = []
    if row:
        lines.append("  ".join(row))
    return "\n".join(lines)


def _format_backtrace(frames: list[dict[str, Any]]) -> str:
    """Format backtrace as GDB-style frame listing."""
    if not frames:
        return "(empty backtrace)"
    lines = []
    for f in frames:
        level = f.get("level", "?")
        func = f.get("function", "??")
        addr = f.get("address", "")
        file = f.get("file", "")
        line = f.get("line", "")
        loc = f" at {file}:{line}" if file and line else ""
        lines.append(f"#{level}  {_FUNC}{func}{_RST} (){loc}  [{_ADDR}{addr}{_RST}]")
        if "locals" in f and f["locals"]:
            for local in f["locals"]:
                if isinstance(local, dict):
                    lname = local.get("name", "?")
                    lval = local.get("value", "?")
                    lines.append(f"      {lname} = {lval}")
    return "\n".join(lines)


def _format_breakpoint(bp: dict[str, Any]) -> str:
    """Format a single breakpoint as confirmation line."""
    bp_id = bp.get("id", "?")
    loc = bp.get("location", "")
    addr = bp.get("address", "")
    file = bp.get("file", "")
    line = bp.get("line", "")
    temp = " (temporary)" if bp.get("temporary") else ""
    cond = f" if {bp.get('condition')}" if bp.get("condition") else ""
    floc = f" ({file}:{line})" if file and line else ""
    return f"Breakpoint #{bp_id} at {loc}{floc} [{addr}]{temp}{cond}"


def _format_breakpoint_list(breakpoints: dict[int, dict]) -> str:
    """Format breakpoint table."""
    if not breakpoints:
        return "No breakpoints set."
    lines = []
    for bp in breakpoints.values():
        bp_id = bp.get("id", "?")
        loc = bp.get("location", "")
        file = bp.get("file", "")
        line = bp.get("line", "")
        enabled = "enabled" if bp.get("enabled", True) else "disabled"
        hits = bp.get("hits", 0)
        temp = ", temporary" if bp.get("temporary") else ""
        floc = f"  {file}:{line}" if file and line else ""
        lines.append(f"  #{bp_id}  {loc}{floc}  [{enabled}, {hits} hit{'s' if hits != 1 else ''}{temp}]")
    return "\n".join(lines)


def _format_disassembly(
    target: str | None, instructions: list[dict[str, Any]], mixed: bool
) -> str:
    """Format disassembly as an assembly listing."""
    header = f"Disassembly of {target}:" if target else "Disassembly:"
    if not instructions:
        return f"{header}\n  (no instructions)"

    lines = [header]
    last_source = None
    for insn in instructions:
        if mixed:
            src_file = insn.get("source_file", "")
            src_line = insn.get("source_line", "")
            src_key = (src_file, src_line) if src_file and src_line else None
            if src_key and src_key != last_source:
                lines.append(f"  {src_file}:{src_line}")
                last_source = src_key

        addr = insn.get("address", "")
        func = insn.get("function", "")
        offset = insn.get("offset", "")
        inst = insn.get("instruction", "")
        func_label = f" <{func}+{offset}>" if func and offset else ""
        lines.append(f"    {addr}{func_label}\t{inst}")

    return "\n".join(lines)


def _format_peripheral_decode(
    periph: str, reg: str, addr: int, raw: int, fields: list
) -> str:
    """Format a decoded peripheral register with bitfields."""
    lines = [f"{periph}.{reg} @ 0x{addr:08x} = 0x{raw:08x}"]
    for f in fields:
        name = f.get("name", "?")
        val = f.get("value", 0)
        bits = f.get("bits", "")
        desc = f.get("description", "")
        enum_name = f.get("enum", "")
        detail = f"  ({enum_name})" if enum_name else (f"  ({desc})" if desc else "")
        lines.append(f"  {name:<12s} [{bits}] = 0x{val:x}{detail}")
    return "\n".join(lines)


def _format_peripheral_all(peripheral: str, registers: dict[str, Any]) -> str:
    """Format all registers in a peripheral."""
    lines = [f"Peripheral: {peripheral}"]
    for reg_name, reg_info in registers.items():
        if "error" in reg_info:
            lines.append(f"  {reg_name}: ERROR {reg_info['error']}")
            continue
        raw = reg_info.get("raw", "?")
        addr = reg_info.get("address", "?")
        fields = reg_info.get("fields", {})
        field_summary = ", ".join(
            f"{fn}={fv.get('value', '?')}" for fn, fv in fields.items()
        ) if fields else ""
        lines.append(f"  {reg_name:<12s} {addr} = {raw}  {field_summary}")
    return "\n".join(lines)


def _format_fault_report(report: dict[str, Any]) -> str:
    """Format Cortex-M fault analysis as diagnostic report."""
    lines = ["=== Cortex-M Fault Analysis ===", ""]

    # Fault registers
    fregs = report.get("fault_registers", {})
    if fregs:
        lines.append("Fault Registers:")
        for name in ("CFSR", "HFSR", "MMFAR", "BFAR"):
            val = fregs.get(name, "?")
            lines.append(f"  {name:<6s} = {val}")
        lines.append("")

    # Active fault bits
    fbits = report.get("fault_bits", [])
    if fbits:
        lines.append("Active Fault Bits:")
        for fb in fbits:
            lines.append(f"  [{fb['register']}] {fb['bit']}: {fb['description']}")
        lines.append("")
    else:
        lines.append("Active Fault Bits: none")
        lines.append("")

    # Stacked frame
    sf = report.get("stacked_frame")
    if sf:
        lines.append(f"Stacked Exception Frame (at SP {report.get('core_registers', {}).get('sp', '?')}):")
        lines.append(f"  R0={sf['R0']}  R1={sf['R1']}  R2={sf['R2']}  R3={sf['R3']}")
        lines.append(f"  R12={sf['R12']}  LR={sf['LR']}  PC={sf['PC']}  xPSR={sf['xPSR']}")
        fpc = report.get("faulting_pc", "")
        if fpc:
            lines.append(f"  Faulting PC: {fpc}")
        lines.append("")

    # Core registers
    cregs = report.get("core_registers")
    if cregs:
        lines.append("Core Registers (in handler):")
        lines.append("  " + "  ".join(f"{k}={v}" for k, v in cregs.items()))
        lines.append("")

    # Backtrace
    bt = report.get("backtrace", [])
    if bt:
        lines.append("Backtrace:")
        for f in bt:
            level = f.get("level", "?")
            func = f.get("function", "??")
            file = f.get("file", "")
            line_no = f.get("line", "")
            loc = f" at {file}:{line_no}" if file and line_no else ""
            lines.append(f"  #{level}  {func}{loc}")
        lines.append("")

    # Diagnosis
    diag = report.get("diagnosis", [])
    if diag:
        lines.append("Diagnosis:")
        for d in diag:
            lines.append(f"  {d}")
    else:
        lines.append("Diagnosis: no specific fault pattern identified.")

    return "\n".join(lines)


def _format_dashboard(
    regs: dict[str, str],
    disasm_lines: list[str],
    stack_dump: str,
    sp_val: int,
    bt_lines: list[str],
    source_text: str | None,
    data_dump: str | None,
    data_addr: int | None,
    style: str = "softice",
) -> str:
    """Format the SoftICE/IDA-style composite debug view.

    style="softice": Dense, maximum info per line, minimal decoration (SoftICE green screen)
    style="ida": Boxed panels with clear headers, grouped registers (IDA Debug Bridge)
    """
    if style == "ida":
        return _format_dashboard_ida(
            regs, disasm_lines, stack_dump, sp_val,
            bt_lines, source_text, data_dump, data_addr,
        )

    W = 120  # line width -- terminals are wide, use the space
    out: list[str] = []

    # ── Registers (SoftICE-style: NAME=VALUE, dense, flags decoded) ──
    out.append(f"{_SECTION}\u2500\u2500\u2500 registers {'─' * (W - 15)}{_RST}")

    # Detect architecture from register names and format accordingly
    reg_lower = {k.lower() for k in regs}
    flag_line = ""

    if "rax" in reg_lower or "eax" in reg_lower:
        # x86 / x86_64
        if "rax" in reg_lower:
            order = ["rax","rbx","rcx","rdx","rsi","rdi","rbp","rsp",
                     "r8","r9","r10","r11","r12","r13","r14","r15","rip"]
            flag_reg = "eflags"
        else:
            order = ["eax","ebx","ecx","edx","esi","edi","ebp","esp","eip"]
            flag_reg = "eflags"
        # Decode x86 EFLAGS (SoftICE-style: o d I s Z a P c)
        for fr in (flag_reg, "eflags", "rflags"):
            if fr in regs:
                raw = int(regs[fr], 0)
                o = "O" if raw & (1 << 11) else "o"
                d = "D" if raw & (1 << 10) else "d"
                i = "I" if raw & (1 << 9) else "i"
                s = "S" if raw & (1 << 7) else "s"
                z = "Z" if raw & (1 << 6) else "z"
                a = "A" if raw & (1 << 4) else "a"
                p = "P" if raw & (1 << 2) else "p"
                c = "C" if raw & (1 << 0) else "c"
                def _cf(letter, is_set):
                    return f"{_FLAG_SET}{letter}{_RST}" if is_set else f"{_FLAG_CLR}{letter}{_RST}"
                flags = [_cf(l, raw & (1 << b)) for b, l in [(11,"O"),(10,"D"),(9,"I"),(7,"S"),(6,"Z"),(4,"A"),(2,"P"),(0,"C")]]
                flag_line = f"  [ {' '.join(flags)} ]"
                break
    elif any(r.startswith("$") and r[1:].isdigit() for r in reg_lower) or "hi" in reg_lower:
        # MIPS
        order = [f"${i}" for i in range(32)] + ["hi", "lo", "pc"]
        # MIPS has no single flags register, status is in CP0
    else:
        # ARM / Cortex-M / AArch64 (default)
        order = ["r0","r1","r2","r3","r4","r5","r6","r7",
                 "r8","r9","r10","r11","r12","sp","lr","pc"]
        # Try AArch64 register names
        if "x0" in reg_lower:
            order = [f"x{i}" for i in range(31)] + ["sp", "pc"]
        # Decode ARM xPSR/CPSR flags
        for fr in ("xpsr", "cpsr"):
            if fr in regs:
                raw = int(regs[fr], 0)
                def _cf(letter, is_set):
                    return f"{_FLAG_SET}{letter}{_RST}" if is_set else f"{_FLAG_CLR}{letter}{_RST}"
                flags = [_cf(l, raw & (1 << b)) for b, l in [(31,"N"),(30,"Z"),(29,"C"),(28,"V"),(27,"Q"),(24,"T")]]
                flag_line = f"  [ {' '.join(flags)} ]"
                break

    # Emit register rows (8 per line for wide terminal, SoftICE density)
    row: list[str] = []
    shown_regs: set[str] = set()
    for name in order:
        # Match case-insensitively
        matched = None
        for rn in regs:
            if rn.lower() == name.lower():
                matched = rn
                break
        if not matched:
            continue
        shown_regs.add(matched.lower())
        val = regs[matched].replace("0x", "").upper()
        # Zero-pad based on value width (8 for 32-bit, 16 for 64-bit)
        pad = 16 if len(val) > 8 else 8
        val = val.zfill(pad)
        label = name.upper()
        row.append(_vpad(f"{label}={_REG_VAL}{val}{_RST}", 14))
        if len(row) == 8:
            out.append(" " + "  ".join(row))
            row = []
    # Any remaining ordered registers
    if row:
        out.append(" " + "  ".join(row))

    # Flag register + any unshown registers (status regs, etc.)
    extras: list[str] = []
    for rn, rv in regs.items():
        if rn.lower() not in shown_regs and rn.lower() not in ("", ):
            val = rv.replace("0x", "").upper().zfill(8)
            extras.append(f"{rn.upper()}={val}")
            shown_regs.add(rn.lower())
    if extras or flag_line:
        extra_str = "  ".join(extras[:4])
        out.append(f" {extra_str}{flag_line}")

    # ── Code (disassembly around PC with > marker) ──
    out.append(f"{_SECTION}\u2500\u2500\u2500 code {'─' * (W - 10)}{_RST}")
    for line in disasm_lines:
        if "<<<" in line:
            out.append(f"{_PC_LINE}{line}{_RST}")
        else:
            out.append(line)

    # ── Source (if available) ──
    if source_text and source_text.strip():
        out.append(f"{_SECTION}\u2500\u2500\u2500 source {'─' * (W - 12)}{_RST}")
        for line in source_text.strip().split("\n")[:12]:
            out.append(f" {line}")

    # ── Stack ──
    out.append(f"{_SECTION}\u2500\u2500\u2500 stack [SP={sp_val:08X}] {'─' * (W - 24)}{_RST}")
    out.append(stack_dump)

    # ── Data watch (optional) ──
    if data_dump and data_addr is not None:
        out.append(f"{_SECTION}\u2500\u2500\u2500 data [0x{data_addr:08X}] {'─' * (W - 26)}{_RST}")
        out.append(data_dump)

    # ── Backtrace ──
    out.append(f"{_SECTION}\u2500\u2500\u2500 backtrace {'─' * (W - 14)}{_RST}")
    for line in bt_lines:
        out.append(line)

    out.append(f"{_SECTION}{'─' * W}{_RST}")
    return "\n".join(out)


def _format_dashboard_ida(
    regs: dict[str, str],
    disasm_lines: list[str],
    stack_dump: str,
    sp_val: int,
    bt_lines: list[str],
    source_text: str | None,
    data_dump: str | None,
    data_addr: int | None,
) -> str:
    """IDA Debug Bridge style: boxed panels, grouped registers, clear structure."""
    # Use plain text box drawing - no right-side borders to avoid alignment issues.
    # This matches how IDA actually renders in a variable-width context.
    W = 120
    out: list[str] = []

    def header(title: str) -> str:
        return f"┌─ {title} {'─' * (W - len(title) - 4)}┐"

    def divider(title: str) -> str:
        return f"├─ {title} {'─' * (W - len(title) - 4)}┤"

    def footer() -> str:
        return f"└{'─' * (W - 1)}┘"

    # ── Detect architecture ──
    reg_lower = {k.lower(): k for k in regs}
    gp_names: list[str]
    sys_names: list[str]

    if "rax" in reg_lower or "eax" in reg_lower:
        gp_names = (["rax","rbx","rcx","rdx","rsi","rdi","rbp","rsp","r8","r9",
                      "r10","r11","r12","r13","r14","r15","rip"] if "rax" in reg_lower
                     else ["eax","ebx","ecx","edx","esi","edi","ebp","esp","eip"])
        sys_names = ["eflags", "rflags", "cs", "ss", "ds", "es", "fs", "gs"]
    elif "x0" in reg_lower:
        gp_names = [f"x{i}" for i in range(31)] + ["sp", "pc"]
        sys_names = ["cpsr", "fpsr", "fpcr"]
    else:
        gp_names = ["r0","r1","r2","r3","r4","r5","r6","r7",
                     "r8","r9","r10","r11","r12","sp","lr","pc"]
        sys_names = ["xpsr","cpsr","msp","psp","primask","basepri","faultmask","control","fpscr"]

    # ── CPU Registers panel ──
    out.append(header("CPU Registers"))

    # Build register entries with colored values
    gp_entries: list[str] = []
    for name in gp_names:
        actual = reg_lower.get(name.lower())
        if actual:
            val = regs[actual].replace("0x", "").upper()
            pad = 16 if len(val) > 8 else 8
            gp_entries.append(f"{name.upper():<4s} {_REG_VAL}{val.zfill(pad)}{_RST}")

    # 4-column grid (or 2 for 64-bit regs)
    cols = 2 if any(len(e) > 18 for e in gp_entries) else 4
    col_w = 27 if cols == 4 else 55
    for i in range(0, len(gp_entries), cols):
        chunk = gp_entries[i:i + cols]
        out.append("│ " + "  ".join(_vpad(e, col_w) for e in chunk))

    # Status registers with flag decode
    sys_entries: list[str] = []
    for name in sys_names:
        actual = reg_lower.get(name.lower())
        if not actual:
            continue
        val = regs[actual].replace("0x", "").upper().zfill(8)
        flag_str = ""
        if name.lower() in ("xpsr", "cpsr"):
            raw = int(regs[actual], 0)
            flags = []
            for bit, letter in [(31,"N"),(30,"Z"),(29,"C"),(28,"V"),(27,"Q"),(24,"T")]:
                if raw & (1 << bit):
                    flags.append(f"{_FLAG_SET}{letter}{_RST}")
                else:
                    flags.append(f"{_FLAG_CLR}{letter}{_RST}")
            flag_str = f"  [{' '.join(flags)}]"
        elif name.lower() in ("eflags", "rflags"):
            raw = int(regs[actual], 0)
            flags = []
            for bit, letter in [(11,"OF"),(10,"DF"),(9,"IF"),(7,"SF"),(6,"ZF"),(4,"AF"),(2,"PF"),(0,"CF")]:
                if raw & (1 << bit):
                    flags.append(f"{_FLAG_SET}{letter}{_RST}")
                else:
                    flags.append(f"{_FLAG_CLR}{letter}{_RST}")
            flag_str = f"  [{' '.join(flags)}]"
        sys_entries.append(f"{name.upper():<8s} {_REG_VAL}{val}{_RST}{flag_str}")

    if sys_entries:
        out.append(f"│{'─' * (W - 1)}│")
        for e in sys_entries:
            out.append(f"│ {e}")

    # Extra registers
    shown = {n.lower() for n in gp_names + sys_names}
    for k, v in regs.items():
        if k.lower() not in shown:
            val = v.replace("0x", "").upper().zfill(8)
            out.append(f"│ {k.upper():<8s} {_REG_VAL}{val}{_RST}")

    # ── Disassembly panel ──
    out.append(divider("Disassembly"))
    if disasm_lines:
        for line in disasm_lines:
            if "<<<" in line:
                out.append(f"│ {_PC_LINE}{line}{_RST}")
            else:
                out.append(f"│ {line}")
    else:
        out.append("│ (no disassembly)")

    # ── Source panel (optional) ──
    if source_text and source_text.strip():
        out.append(divider("Source"))
        for line in source_text.strip().split("\n")[:10]:
            out.append(f"│ {line}")

    # ── Stack panel ──
    out.append(divider(f"Stack [SP=0x{sp_val:08X}]"))
    for line in stack_dump.split("\n"):
        out.append(f"│ {line}")

    # ── Data panel (optional) ──
    if data_dump and data_addr is not None:
        out.append(divider(f"Data [0x{data_addr:08X}]"))
        for line in data_dump.split("\n"):
            out.append(f"│ {line}")

    # ── Backtrace panel ──
    out.append(divider("Backtrace"))
    for line in bt_lines:
        out.append(f"│ {line}")

    out.append(footer())
    return "\n".join(out)


# ── Server ───────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastMCP):
    # Load any saved sessions on startup
    for sid in _store.list_sessions():
        saved = _store.load(sid)
        if saved:
            _sessions[sid] = DebugSession(sid)
    yield
    # Cleanup on shutdown
    for session in _sessions.values():
        try:
            await session.disconnect()
        except Exception:
            pass


def create_server() -> FastMCP:
    mcp = FastMCP(
        name="jtag-debug",
        version="0.1.0",
        instructions="JTAG/GDB debugging of embedded targets with Saleae Logic 2 integration",
    )

    # ── Session Management (Tier 0) ─────────────────────────────────

    @mcp.tool()
    async def session_state(session_id: str):
        """Get full session state: GDB state, target info, breakpoints, stop reason, Saleae status, loaded SVD.
        Call this first in any debug conversation and after any state-changing operation."""
        session = _get_session(session_id)
        decoder = _get_decoder(session_id)
        saleae = _saleae_connections.get(session_id)

        conn = session.connection
        gdb_info = f"{conn.gdb_path} ({conn.mi_version}, PID {conn.pid})" if conn else "not connected"
        ti = session.target_info
        target_str = f"{ti.get('host', '?')}:{ti.get('port', '?')}" if ti else "none"
        elf_str = ti.get("elf", "none") if ti else "none"

        lines = [
            f"Session: {session_id} | State: {session.state.value} | GDB: {gdb_info}",
            f"Target: {target_str} | ELF: {elf_str}",
        ]

        stop = session.last_stop
        if stop:
            lines.append(_format_stop_line(stop))
        else:
            lines.append("Execution: no stop recorded")

        svd_str = "loaded" if decoder else "not loaded"
        saleae_str = "connected" if saleae else "not connected"
        lines.append(f"SVD: {svd_str} | Saleae: {saleae_str}")

        if session.breakpoints:
            lines.append("Breakpoints:")
            lines.append(_format_breakpoint_list(session.breakpoints))

        return _tool_result("\n".join(lines), {
            "session_id": session_id,
            "state": session.state.value,
            "target": session.target_info,
            "execution": session.last_stop or {},
            "breakpoints": list(session.breakpoints.values()),
            "svd_loaded": decoder is not None,
            "saleae_connected": saleae is not None,
        })

    @mcp.tool()
    async def session_list():
        """List all active debug sessions with summary."""
        if not _sessions:
            return "No active sessions."
        lines = [f"Active sessions ({len(_sessions)}):"]
        for sid, session in _sessions.items():
            ti = session.target_info
            target_str = f"{ti.get('host', '?')}:{ti.get('port', '?')}" if ti else "none"
            lines.append(f"  {sid}  [{session.state.value}]  {target_str}")
        return "\n".join(lines)

    @mcp.tool()
    async def session_connect(
        session_id: str,
        target: str = "localhost",
        gdb_port: int = 3333,
        elf_path: str | None = None,
        svd_path: str | None = None,
        gdb_path: str | None = None,
        probe: str | None = None,
    ):
        """Connect to a debug target. Creates or reuses a session.

        Args:
            session_id: Unique session identifier
            target: GDB server host (default localhost)
            gdb_port: GDB server port (default 3333)
            elf_path: Path to ELF binary with debug symbols
            svd_path: Path to SVD file for peripheral register decode
            gdb_path: Override GDB binary path
            probe: Probe type hint for auto-detection
        """
        if session_id in _sessions:
            old = _sessions[session_id]
            if old.state != GDBState.DISCONNECTED:
                try:
                    await old.disconnect()
                except Exception:
                    pass

        session = DebugSession(session_id)
        _sessions[session_id] = session

        result = await session.connect(
            gdb_path=gdb_path,
            target=target,
            port=gdb_port,
            elf_path=elf_path,
        )

        lines = [
            f"Connected: {session_id} -> {target}:{gdb_port}",
            f"GDB: {result.get('gdb_path', '?')} ({result.get('mi_version', '?')}, PID {result.get('pid', '?')})",
        ]
        if elf_path:
            lines.append(f"ELF: {elf_path}")

        # Load SVD if provided
        if svd_path:
            try:
                device = _svd_parser.load(svd_path)
                _svd_decoders[session_id] = RegisterDecoder(device)
                _svd_paths[session_id] = svd_path
                lines.append(f"SVD: {device.name} loaded")
            except Exception as exc:
                lines.append(f"SVD: failed to load ({exc})")

        # Persist
        _store.save(session_id, session.serialize())

        return "\n".join(lines)

    @mcp.tool()
    async def session_disconnect(session_id: str):
        """Disconnect and clean up a debug session."""
        session = _get_session(session_id)
        await session.disconnect()
        _sessions.pop(session_id, None)
        _svd_decoders.pop(session_id, None)
        _svd_paths.pop(session_id, None)
        _saleae_connections.pop(session_id, None)
        _saleae_coordinators.pop(session_id, None)
        _store.delete(session_id)
        return f"Disconnected: {session_id}"

    # ── Register and Memory (Tier 0 reads) ──────────────────────────

    @mcp.tool()
    async def register_read(
        session_id: str, registers: list[str] | None = None
    ):
        """Read CPU core registers. Returns {name: hex_value} dict.

        Args:
            session_id: Session ID
            registers: Specific register names to read, or None for all
        """
        session = _get_session(session_id)
        regs = await session.read_registers(registers)
        return _tool_result(_format_registers(regs), regs)

    @mcp.tool()
    async def peripheral_read(
        session_id: str, peripheral: str, register: str
    ):
        """Read and decode a peripheral register via SVD.
        Returns decoded bitfields with names, values, and descriptions.

        Args:
            session_id: Session ID
            peripheral: SVD peripheral name (e.g., "SPI1", "GPIOA", "RCC")
            register: Register name (e.g., "CR1", "SR", "CFGR")
        """
        session = _get_session(session_id)
        decoder = _get_decoder(session_id)
        if not decoder:
            raise ValueError("No SVD loaded. Provide svd_path in session_connect.")

        addr = decoder.get_register_address(peripheral, register)
        raw = await session.read_memory(addr, 4)
        raw_value = struct.unpack("<I", raw)[0]

        decode = decoder.decode_register(peripheral, register, raw_value)
        fields = [
            {
                "name": f.name,
                "value": f.value,
                "bits": f.bit_range,
                "description": f.description,
                "enum": f.enumerated_name,
            }
            for f in decode.fields
        ]
        return _format_peripheral_decode(
            decode.peripheral, decode.register, decode.address, decode.raw_value, fields
        )

    @mcp.tool()
    async def peripheral_read_all(
        session_id: str, peripheral: str
    ):
        """Read and decode ALL registers in a peripheral.

        Args:
            session_id: Session ID
            peripheral: SVD peripheral name
        """
        session = _get_session(session_id)
        decoder = _get_decoder(session_id)
        if not decoder:
            raise ValueError("No SVD loaded.")

        registers: dict[str, Any] = {}
        for reg_name in decoder.list_registers(peripheral):
            try:
                addr = decoder.get_register_address(peripheral, reg_name)
                raw = await session.read_memory(addr, 4)
                raw_value = struct.unpack("<I", raw)[0]
                decode = decoder.decode_register(peripheral, reg_name, raw_value)
                registers[reg_name] = {
                    "address": f"0x{decode.address:08x}",
                    "raw": f"0x{decode.raw_value:08x}",
                    "fields": {
                        f.name: {
                            "value": f.value,
                            "bits": f.bit_range,
                            "enum": f.enumerated_name,
                        }
                        for f in decode.fields
                    },
                }
            except Exception as exc:
                registers[reg_name] = {"error": str(exc)}

        return _format_peripheral_all(peripheral, registers)

    @mcp.tool()
    async def memory_read(
        session_id: str, address: str, length: int = 256
    ):
        """Read target memory. Returns hex dump with ASCII sidebar.

        Args:
            session_id: Session ID
            address: Start address (hex string like "0x20000000" or decimal)
            length: Bytes to read (max 1024)
        """
        session = _get_session(session_id)
        length = min(length, 1024)
        addr = int(address, 0) if isinstance(address, str) else address
        data = await session.read_memory(addr, length)
        text = f"Memory at 0x{addr:08x} ({len(data)} bytes):\n{_format_hex_dump(data, addr)}"
        return _tool_result(text, {
            "address": f"0x{addr:08x}", "length": len(data), "hex": data.hex(),
        })

    @mcp.tool()
    async def svd_lookup(session_id: str, query: str):
        """Search SVD for peripherals/registers matching a query.

        Args:
            session_id: Session ID
            query: Search term (e.g., "SPI", "clock", "CFSR")
        """
        decoder = _get_decoder(session_id)
        if not decoder:
            raise ValueError("No SVD loaded.")

        query_upper = query.upper()
        matches: list[str] = []

        for periph_name in decoder.list_peripherals():
            if query_upper in periph_name.upper():
                matches.append(f"  [peripheral] {periph_name}")
            for reg_name in decoder.list_registers(periph_name):
                if query_upper in reg_name.upper():
                    addr = decoder.get_register_address(periph_name, reg_name)
                    matches.append(f"  [register]   {periph_name}.{reg_name} @ 0x{addr:08x}")

        if not matches:
            return f'No SVD matches for "{query}".'
        truncated = " (truncated)" if len(matches) > 50 else ""
        return f'SVD matches for "{query}" ({len(matches[:50])} results{truncated}):\n' + "\n".join(matches[:50])

    # ── Execution Control (Tier 1) ──────────────────────────────────

    @mcp.tool()
    async def target_continue(session_id: str):
        """Continue target execution. Returns stop reason when target halts."""
        session = _get_session(session_id)
        stop = await session.continue_execution()
        return _tool_result(_format_stop_line(stop), stop)

    @mcp.tool()
    async def target_step(
        session_id: str, step_type: str = "into", count: int = 1
    ):
        """Step execution.

        Args:
            session_id: Session ID
            step_type: "into" (step into functions), "over" (step over), "out" (step out)
            count: Number of steps
        """
        session = _get_session(session_id)
        stop = await session.step(step_type, count)
        return _tool_result(_format_stop_line(stop), stop)

    @mcp.tool()
    async def target_interrupt(session_id: str):
        """Interrupt a running target. Returns stop info."""
        session = _get_session(session_id)
        stop = await session.interrupt()
        return _tool_result(_format_stop_line(stop), stop)

    @mcp.tool()
    async def breakpoint_set(
        session_id: str,
        location: str,
        condition: str | None = None,
        temporary: bool = False,
    ):
        """Set a breakpoint.

        Args:
            session_id: Session ID
            location: Function name, file:line, or *0xADDRESS
            condition: Optional condition expression
            temporary: If True, breakpoint auto-deletes after first hit
        """
        session = _get_session(session_id)
        bp = await session.set_breakpoint(location, condition, temporary)
        return _tool_result(_format_breakpoint(bp), bp)

    @mcp.tool()
    async def breakpoint_remove(session_id: str, breakpoint_id: int):
        """Remove a breakpoint by ID."""
        session = _get_session(session_id)
        await session.remove_breakpoint(breakpoint_id)
        return f"Removed breakpoint #{breakpoint_id}"

    @mcp.tool()
    async def breakpoint_list(session_id: str):
        """List all breakpoints with hit counts."""
        session = _get_session(session_id)
        return _format_breakpoint_list(session.breakpoints)

    @mcp.tool()
    async def backtrace(session_id: str, full: bool = False):
        """Get stack backtrace.

        Args:
            session_id: Session ID
            full: If True, include local variables for each frame
        """
        session = _get_session(session_id)
        frames = await session.backtrace(full=full)
        return _tool_result(_format_backtrace(frames), {"frames": frames})

    @mcp.tool()
    async def evaluate(session_id: str, expression: str):
        """Evaluate an expression in the current stack frame.

        Args:
            session_id: Session ID
            expression: C expression to evaluate (e.g., "sizeof(struct foo)", "array[3]")
        """
        session = _get_session(session_id)
        result = await session.evaluate(expression)
        return _tool_result(f"{expression} = {result}", {"expression": expression, "value": result})

    # ── Source, Symbols, and Disassembly (Tier 0) ───────────────────

    @mcp.tool()
    async def disassemble(
        session_id: str,
        function: str | None = None,
        start_addr: str | None = None,
        end_addr: str | None = None,
        count: int = 50,
        mixed: bool = True,
    ):
        """Disassemble code at current PC, a function, or address range.

        Returns assembly instructions with optional interleaved source lines.

        Args:
            session_id: Session ID
            function: Function name to disassemble (e.g., "SPI1_IRQHandler")
            start_addr: Start address (hex) for range disassembly
            end_addr: End address (hex) for range disassembly
            count: Max instructions to return (default 50)
            mixed: Interleave source lines with assembly (default True)
        """
        session = _get_session(session_id)
        assert session.connection is not None

        # -data-disassemble mode: 0=asm only, 1=mixed source+asm,
        # 4=opcodes+asm, 5=opcodes+mixed
        mode = 1 if mixed else 0

        if function:
            # Disassemble entire function
            result = await session.connection.send(
                f'-data-disassemble -a {function} -- {mode}'
            )
        elif start_addr and end_addr:
            s = int(start_addr, 0)
            e = int(end_addr, 0)
            result = await session.connection.send(
                f'-data-disassemble -s 0x{s:08x} -e 0x{e:08x} -- {mode}'
            )
        else:
            # Disassemble around current PC
            regs = await session.read_registers(["pc"])
            pc = regs.get("pc", "0x0")
            pc_val = int(pc, 0)
            result = await session.connection.send(
                f'-data-disassemble -s 0x{pc_val:08x} -e 0x{pc_val + count * 4:08x} -- {mode}'
            )

        asm_insns = result.results.get("asm_insns", [])
        instructions: list[dict[str, Any]] = []

        for entry in asm_insns[:count]:
            if isinstance(entry, dict):
                # Mixed mode returns {src_and_asm_line: {line, file, ...}, insn: [...]}
                # or direct instruction dicts
                if "src_and_asm_line" in entry:
                    src_info = entry["src_and_asm_line"]
                    insns = entry.get("line_asm_insn", [])
                    src_line = {
                        "source_file": src_info.get("file", ""),
                        "source_line": src_info.get("line", ""),
                        "source_text": src_info.get("fullname", ""),
                    }
                    for insn in insns:
                        if isinstance(insn, dict):
                            instructions.append({
                                "address": insn.get("address", ""),
                                "function": insn.get("func-name", ""),
                                "offset": insn.get("offset", ""),
                                "instruction": insn.get("inst", ""),
                                "opcodes": insn.get("opcodes", ""),
                                **src_line,
                            })
                elif "address" in entry or "inst" in entry:
                    instructions.append({
                        "address": entry.get("address", ""),
                        "function": entry.get("func-name", ""),
                        "offset": entry.get("offset", ""),
                        "instruction": entry.get("inst", ""),
                        "opcodes": entry.get("opcodes", ""),
                    })

        target_name = function or (f"0x{int(start_addr, 0):08x}" if start_addr else "current PC")
        return _format_disassembly(target_name, instructions, mixed)

    @mcp.tool()
    async def source_context(
        session_id: str,
        file: str | None = None,
        line: int | None = None,
        function: str | None = None,
        context_lines: int = 10,
    ):
        """Show source code around current stop location, a file:line, or function.

        Args:
            session_id: Session ID
            file: Source file path
            line: Line number
            function: Function name (alternative to file:line)
            context_lines: Lines of context above/below (default 10)
        """
        session = _get_session(session_id)
        assert session.connection is not None

        if function:
            # Use GDB to find function location
            await session.connection.send(
                f'-interpreter-exec console "info line {function}"'
            )
            output = await session.raw_command(f"list {function}")
        elif file and line:
            start = max(1, line - context_lines)
            end = line + context_lines
            output = await session.raw_command(f"list {file}:{start},{end}")
        else:
            # Current location
            output = await session.raw_command(
                f"list *$pc-{context_lines * 4},*$pc+{context_lines * 4}"
            )
            # Fallback to listing around current frame
            if not output.strip() or "No" in output:
                output = await session.raw_command("list")

        # Also get current frame info
        frames = await session.backtrace(full=False)
        current_frame = frames[0] if frames else {}

        func_name = current_frame.get("function", "??")
        src_file = file or current_frame.get("file", "")
        src_line = line or current_frame.get("line", "")
        header = f"Source at {_FUNC}{func_name}{_RST} ({src_file}:{src_line}):" if src_file else f"Source at {_FUNC}{func_name}{_RST}:"
        return f"{header}\n{output}" if output.strip() else f"{header}\n(no source available)"

    @mcp.tool()
    async def symbol_info(
        session_id: str,
        symbol: str | None = None,
        address: str | None = None,
    ):
        """Look up symbol information: type, size, address, and source location.

        Args:
            session_id: Session ID
            symbol: Symbol name (variable, function, type)
            address: Address to find symbol for (reverse lookup)
        """
        session = _get_session(session_id)
        assert session.connection is not None

        lines: list[str] = []

        if symbol:
            lines.append(f"Symbol: {symbol}")

            try:
                type_output = await session.raw_command(f"ptype {symbol}")
                lines.append(f"Type: {type_output.strip()}")
            except GDBError:
                pass

            try:
                addr_output = await session.raw_command(f"print &{symbol}")
                lines.append(f"Address: {addr_output.strip()}")
            except GDBError:
                pass

            try:
                val = await session.evaluate(symbol)
                lines.append(f"Value: {val}")
            except GDBError:
                pass

            try:
                size_val = await session.evaluate(f"sizeof({symbol})")
                lines.append(f"Size: {size_val} bytes")
            except GDBError:
                pass

            try:
                info_output = await session.raw_command(f"info symbol {symbol}")
                lines.append(f"Info: {info_output.strip()}")
            except GDBError:
                pass

        elif address:
            addr = int(address, 0)
            lines.append(f"Address: 0x{addr:08x}")

            try:
                info_output = await session.raw_command(f"info symbol 0x{addr:08x}")
                lines.append(f"Symbol: {info_output.strip()}")
            except GDBError:
                lines.append("Symbol: (none)")

            try:
                line_output = await session.raw_command(f"info line *0x{addr:08x}")
                lines.append(f"Source: {line_output.strip()}")
            except GDBError:
                pass

        return "\n".join(lines) if lines else "No symbol or address specified."

    # ── Write Operations (Tier 2) ───────────────────────────────────

    @mcp.tool()
    async def memory_write(
        session_id: str, address: str, hex_data: str, confirmed: bool = False
    ):
        """Write data to target memory. DESTRUCTIVE: requires confirmed=True.

        Args:
            session_id: Session ID
            address: Target address (hex string)
            hex_data: Hex string of bytes to write
            confirmed: Must be True to proceed
        """
        warning = require_confirmation(
            "memory_write", {"confirmed": confirmed},
            f"write {len(hex_data)//2} bytes to {address}"
        )
        if warning:
            return f"WARNING: {warning}"

        session = _get_session(session_id)
        addr = int(address, 0)
        data = bytes.fromhex(hex_data)
        await session.write_memory(addr, data)
        return f"Written {len(data)} bytes to 0x{addr:08x}"

    @mcp.tool()
    async def register_write(
        session_id: str, register: str, value: str, confirmed: bool = False
    ):
        """Write a CPU register. DESTRUCTIVE: requires confirmed=True.

        Args:
            session_id: Session ID
            register: Register name (e.g., "r0", "pc", "sp")
            value: Value to write (hex or decimal)
        """
        warning = require_confirmation(
            "register_write", {"confirmed": confirmed},
            f"set {register} = {value}"
        )
        if warning:
            return f"WARNING: {warning}"

        session = _get_session(session_id)
        assert session.connection is not None
        await session.connection.send(
            f'-gdb-set ${{register}} = {value}'.replace("${register}", register)
        )
        return f"Written: {register} = {value}"

    @mcp.tool()
    async def peripheral_write(
        session_id: str,
        peripheral: str,
        register: str,
        value: int | None = None,
        field: str | None = None,
        field_value: int | None = None,
        confirmed: bool = False,
    ):
        """Write a peripheral register, optionally by field (read-modify-write).
        DESTRUCTIVE: requires confirmed=True.

        Args:
            session_id: Session ID
            peripheral: SVD peripheral name
            register: Register name
            value: Full register value (if writing whole register)
            field: Field name (for single-field write)
            field_value: Value for the field
            confirmed: Must be True
        """
        desc = f"write {peripheral}.{register}"
        if field:
            desc += f".{field} = {field_value}"
        else:
            desc += f" = 0x{value:08x}" if value is not None else ""

        warning = require_confirmation(
            "peripheral_write", {"confirmed": confirmed}, desc
        )
        if warning:
            return f"WARNING: {warning}"

        session = _get_session(session_id)
        decoder = _get_decoder(session_id)
        if not decoder:
            raise ValueError("No SVD loaded.")

        addr = decoder.get_register_address(peripheral, register)

        if field and field_value is not None:
            # Read-modify-write
            current_raw = await session.read_memory(addr, 4)
            current = struct.unpack("<I", current_raw)[0]
            new_value = decoder.encode_field_value(
                peripheral, register, field, field_value, current
            )
        elif value is not None:
            new_value = value
        else:
            raise ValueError("Provide either value or field+field_value")

        data = struct.pack("<I", new_value)
        await session.write_memory(addr, data)

        # Read back and decode
        readback = await session.read_memory(addr, 4)
        readback_value = struct.unpack("<I", readback)[0]
        decode = decoder.decode_register(peripheral, register, readback_value)

        fields = [
            {"name": f.name, "value": f.value, "bits": f.bit_range, "description": "", "enum": ""}
            for f in decode.fields
        ]
        return (
            f"Written: {peripheral}.{register} @ 0x{addr:08x} = 0x{new_value:08x}\n"
            f"Readback:\n{_format_peripheral_decode(peripheral, register, addr, readback_value, fields)}"
        )

    @mcp.tool()
    async def target_reset(
        session_id: str, halt: bool = True, confirmed: bool = False
    ):
        """Reset the target. DESTRUCTIVE: requires confirmed=True.

        Args:
            session_id: Session ID
            halt: Halt after reset (default True)
            confirmed: Must be True
        """
        warning = require_confirmation(
            "target_reset", {"confirmed": confirmed},
            f"reset target ({'halt' if halt else 'run'})"
        )
        if warning:
            return f"WARNING: {warning}"

        session = _get_session(session_id)
        await session.reset(halt=halt)
        return f"Target reset ({'halted' if halt else 'running'})"

    @mcp.tool()
    async def flash_write(
        session_id: str,
        elf_path: str,
        verified: bool = True,
        confirmed: bool = False,
    ):
        """Flash firmware to target. DESTRUCTIVE: requires confirmed=True.

        Args:
            session_id: Session ID
            elf_path: Path to ELF file
            verified: Verify after flash (default True)
            confirmed: Must be True
        """
        warning = require_confirmation(
            "flash_write", {"confirmed": confirmed},
            f"flash {elf_path} to target"
        )
        if warning:
            return f"WARNING: {warning}"

        session = _get_session(session_id)
        result = await session.flash(elf_path, verify=verified)
        verified_str = ", verified" if result.get("verified") else ""
        return f"Flashed: {elf_path}{verified_str}"

    @mcp.tool()
    async def gdb_raw(
        session_id: str, command: str, confirmed: bool = False
    ):
        """Execute a raw GDB command. Dangerous commands require confirmed=True.

        Args:
            session_id: Session ID
            command: Raw GDB command string
            confirmed: Required for dangerous commands
        """
        danger = check_gdb_raw_safety(command)
        if danger:
            warning = require_confirmation(
                "gdb_raw", {"confirmed": confirmed},
                f"{danger} via raw GDB command"
            )
            if warning:
                return f"WARNING: {warning}"

        session = _get_session(session_id)
        output = await session.raw_command(command)
        return output if output.strip() else "(no output)"

    # ── Saleae Tools ────────────────────────────────────────────────

    @mcp.tool()
    async def saleae_connect(
        session_id: str, host: str = "127.0.0.1", port: int = 10430
    ):
        """Connect to Saleae Logic 2 automation API.

        Logic 2 must be running with automation enabled (Settings > Preferences).

        Args:
            session_id: Session ID
            host: Logic 2 host (default 127.0.0.1)
            port: Automation port (default 10430)
        """
        from .saleae.connection import SaleaeConnection
        from .saleae.capture import CaptureCoordinator

        conn = SaleaeConnection(host=host, port=port)
        info = await conn.connect()
        _saleae_connections[session_id] = conn
        _saleae_coordinators[session_id] = CaptureCoordinator(conn)

        # Load saved channel map if available
        saved_map = _store.load_channel_map(session_id)
        if saved_map:
            _saleae_coordinators[session_id].configure_channels(saved_map)
            info["channel_map_restored"] = saved_map

        return info

    @mcp.tool()
    async def saleae_configure(
        session_id: str,
        channel_map: dict[str, int],
        sample_rate: int = 25_000_000,
        voltage: float = 3.3,
    ):
        """Configure Saleae channel mapping and capture parameters.

        Args:
            session_id: Session ID
            channel_map: Signal-to-channel mapping, e.g. {"SCK": 0, "MOSI": 1, "MISO": 2, "CS": 3}
            sample_rate: Digital sample rate in Hz (default 25 MHz)
            voltage: Logic threshold voltage (default 3.3V)
        """
        coord = _saleae_coordinators.get(session_id)
        if not coord:
            raise ValueError("Saleae not connected. Call saleae_connect first.")

        result = coord.configure_channels(channel_map, sample_rate, voltage)
        _store.save_channel_map(session_id, channel_map)
        return result

    @mcp.tool()
    async def saleae_capture_timed(
        session_id: str,
        duration: float = 0.1,
        analyzers: list[dict[str, Any]] | None = None,
    ):
        """Capture bus traffic for a fixed duration.

        Args:
            session_id: Session ID
            duration: Capture duration in seconds (default 0.1)
            analyzers: List of analyzer configs, e.g. [{"type": "SPI", "settings": {...}}]
        """
        coord = _saleae_coordinators.get(session_id)
        if not coord:
            raise ValueError("Saleae not connected.")

        result = await coord.capture_timed(duration=duration, analyzers=analyzers)
        return {
            "duration": result.duration_seconds,
            "sample_rate": result.sample_rate,
            "analyzers": [
                {
                    "type": ar.analyzer_type,
                    "frames": ar.decoded_frames,
                    "frame_count": ar.frame_count,
                    "error_count": ar.error_count,
                    "truncated": ar.truncated,
                }
                for ar in result.analyzer_results
            ],
            "error": result.error,
            "partial": result.partial,
        }

    @mcp.tool()
    async def saleae_capture_triggered(
        session_id: str,
        channel: int,
        edge: str = "rising",
        pre_trigger: float = 0.001,
        post_trigger: float = 0.01,
        analyzers: list[dict[str, Any]] | None = None,
    ):
        """Capture with digital trigger.

        Args:
            session_id: Session ID
            channel: Trigger channel number
            edge: "rising" or "falling"
            pre_trigger: Seconds before trigger
            post_trigger: Seconds after trigger
            analyzers: Analyzer configs
        """
        coord = _saleae_coordinators.get(session_id)
        if not coord:
            raise ValueError("Saleae not connected.")

        result = await coord.capture_triggered(
            trigger_channel=channel,
            edge=edge,
            pre_trigger=pre_trigger,
            post_trigger=post_trigger,
            analyzers=analyzers,
        )
        return {
            "duration": result.duration_seconds,
            "sample_rate": result.sample_rate,
            "analyzers": [
                {
                    "type": ar.analyzer_type,
                    "frames": ar.decoded_frames,
                    "frame_count": ar.frame_count,
                    "error_count": ar.error_count,
                }
                for ar in result.analyzer_results
            ],
            "error": result.error,
            "partial": result.partial,
        }

    @mcp.tool()
    async def saleae_capture_at_breakpoint(
        session_id: str,
        breakpoint_location: str,
        pre_seconds: float = 0.001,
        post_seconds: float = 0.01,
        analyzers: list[dict[str, Any]] | None = None,
    ):
        """Capture bus traffic around a breakpoint hit (closed-loop debug).

        Sets breakpoint, starts capture, resumes target, waits for breakpoint,
        then stops capture and extracts data.

        Args:
            session_id: Session ID
            breakpoint_location: Breakpoint location (function, file:line, *addr)
            pre_seconds: Capture before breakpoint (seconds)
            post_seconds: Capture after breakpoint (seconds)
            analyzers: Analyzer configs
        """
        coord = _saleae_coordinators.get(session_id)
        if not coord:
            raise ValueError("Saleae not connected.")

        session = _get_session(session_id)
        result = await coord.capture_around_breakpoint(
            debug_session=session,
            breakpoint_location=breakpoint_location,
            pre_seconds=pre_seconds,
            post_seconds=post_seconds,
            analyzers=analyzers,
        )
        return {
            "duration": result.duration_seconds,
            "analyzers": [
                {
                    "type": ar.analyzer_type,
                    "frames": ar.decoded_frames,
                    "frame_count": ar.frame_count,
                    "error_count": ar.error_count,
                }
                for ar in result.analyzer_results
            ],
            "error": result.error,
            "partial": result.partial,
        }

    # ── Fault Analysis Composite (Tier 0) ───────────────────────────

    @mcp.tool()
    async def fault_analyze(session_id: str):
        """Comprehensive Cortex-M fault analysis.

        Reads CFSR, HFSR, MMFAR, BFAR, stacked exception frame,
        decodes fault bits, and provides diagnosis.
        """
        session = _get_session(session_id)
        decoder = _get_decoder(session_id)

        report: dict[str, Any] = {"session_id": session_id}

        # Read core fault registers directly by address
        # These addresses are fixed for all Cortex-M3/M4/M7
        FAULT_REGS = {
            "CFSR": 0xE000ED28,   # Configurable Fault Status
            "HFSR": 0xE000ED2C,   # HardFault Status
            "DFSR": 0xE000ED30,   # Debug Fault Status
            "MMFAR": 0xE000ED34,  # MemManage Fault Address
            "BFAR": 0xE000ED38,   # BusFault Address
            "AFSR": 0xE000ED3C,   # Auxiliary Fault Status
        }

        raw_regs: dict[str, int] = {}
        for name, addr in FAULT_REGS.items():
            try:
                data = await session.read_memory(addr, 4)
                raw_regs[name] = struct.unpack("<I", data)[0]
            except Exception:
                raw_regs[name] = 0

        # Decode CFSR bitfields
        cfsr = raw_regs["CFSR"]
        hfsr = raw_regs["HFSR"]
        mmfar = raw_regs["MMFAR"]
        bfar = raw_regs["BFAR"]

        # CFSR is composed of: UFSR[31:16] + BFSR[15:8] + MMFSR[7:0]
        ufsr = (cfsr >> 16) & 0xFFFF
        bfsr = (cfsr >> 8) & 0xFF
        mmfsr = cfsr & 0xFF

        fault_bits: list[dict[str, str]] = []

        # Usage Faults (UFSR)
        ufsr_bits = {
            0: ("UNDEFINSTR", "Undefined instruction executed"),
            1: ("INVSTATE", "Invalid EPSR.T bit or illegal EPSR.IT state -- often a function pointer missing thumb bit (+1)"),
            2: ("INVPC", "Invalid PC load on exception return (corrupted stack or EXC_RETURN)"),
            3: ("NOCP", "Coprocessor access attempted (FPU not enabled?)"),
            4: ("STKOF", "Stack overflow detected (Cortex-M33+)"),
            8: ("UNALIGNED", "Unaligned memory access"),
            9: ("DIVBYZERO", "Division by zero (only if DIV_0_TRP enabled in CCR)"),
        }
        for bit, (name, desc) in ufsr_bits.items():
            if ufsr & (1 << bit):
                fault_bits.append({"register": "UFSR", "bit": name, "description": desc})

        # Bus Faults (BFSR)
        bfsr_bits = {
            0: ("IBUSERR", "Instruction bus error on prefetch"),
            1: ("PRECISERR", "Precise data bus error -- BFAR holds faulting address"),
            2: ("IMPRECISERR", "Imprecise data bus error -- BFAR NOT valid, stacked PC is approximate"),
            3: ("UNSTKERR", "Bus fault on exception unstacking"),
            4: ("STKERR", "Bus fault on exception stacking -- likely stack overflow"),
            7: ("BFARVALID", "BFAR register holds valid fault address"),
        }
        for bit, (name, desc) in bfsr_bits.items():
            if (cfsr >> 8) & (1 << bit):
                fault_bits.append({"register": "BFSR", "bit": name, "description": desc})

        # MemManage Faults (MMFSR)
        mmfsr_bits = {
            0: ("IACCVIOL", "Instruction access violation"),
            1: ("DACCVIOL", "Data access violation -- MMFAR holds faulting address"),
            3: ("MUNSTKERR", "MemManage fault on unstacking"),
            4: ("MSTKERR", "MemManage fault on stacking -- likely stack overflow"),
            5: ("MLSPERR", "MemManage fault during FP lazy state preservation"),
            7: ("MMARVALID", "MMFAR register holds valid fault address"),
        }
        for bit, (name, desc) in mmfsr_bits.items():
            if cfsr & (1 << bit):
                fault_bits.append({"register": "MMFSR", "bit": name, "description": desc})

        # HardFault Status
        hfsr_bits = {
            1: ("VECTTBL", "Vector table read error on exception processing"),
            30: ("FORCED", "Forced HardFault -- escalated from configurable fault (check CFSR)"),
            31: ("DEBUGEVT", "Debug event caused HardFault"),
        }
        for bit, (name, desc) in hfsr_bits.items():
            if hfsr & (1 << bit):
                fault_bits.append({"register": "HFSR", "bit": name, "description": desc})

        report["fault_registers"] = {
            "CFSR": f"0x{cfsr:08x}",
            "HFSR": f"0x{hfsr:08x}",
            "MMFAR": f"0x{mmfar:08x}" if mmfsr & 0x80 else "invalid",
            "BFAR": f"0x{bfar:08x}" if (cfsr >> 8) & 0x80 else "invalid",
        }
        report["fault_bits"] = fault_bits

        # Get backtrace
        try:
            frames = await session.backtrace()
            report["backtrace"] = frames
        except Exception as exc:
            report["backtrace_error"] = str(exc)

        # Get stacked exception frame
        try:
            regs = await session.read_registers(["sp", "lr", "pc", "xpsr"])
            report["core_registers"] = regs

            # Read stacked frame (R0-R3, R12, LR, PC, xPSR at SP)
            sp = int(regs.get("sp", "0x0"), 0)
            if sp:
                stacked_data = await session.read_memory(sp, 32)
                stacked = struct.unpack("<8I", stacked_data)
                report["stacked_frame"] = {
                    "R0": f"0x{stacked[0]:08x}",
                    "R1": f"0x{stacked[1]:08x}",
                    "R2": f"0x{stacked[2]:08x}",
                    "R3": f"0x{stacked[3]:08x}",
                    "R12": f"0x{stacked[4]:08x}",
                    "LR": f"0x{stacked[5]:08x}",
                    "PC": f"0x{stacked[6]:08x}",
                    "xPSR": f"0x{stacked[7]:08x}",
                }
                report["faulting_pc"] = f"0x{stacked[6]:08x}"
        except Exception as exc:
            report["register_error"] = str(exc)

        # Generate diagnosis
        diagnosis: list[str] = []
        for fb in fault_bits:
            if fb["bit"] == "INVSTATE":
                diagnosis.append(
                    "INVSTATE fault: CPU tried to execute in ARM mode or invalid IT state. "
                    "Most common cause: function pointer cast without +1 for thumb bit. "
                    "Check faulting PC -- is it an even address?"
                )
            elif fb["bit"] == "PRECISERR":
                diagnosis.append(
                    f"Precise bus fault at address {report['fault_registers']['BFAR']}. "
                    "Bad pointer dereference or DMA targeting invalid memory."
                )
            elif fb["bit"] == "IMPRECISERR":
                diagnosis.append(
                    "Imprecise bus fault: write to invalid address, but stacked PC is "
                    "approximate (pipeline). Check recent writes before faulting PC."
                )
            elif fb["bit"] == "STKERR" or fb["bit"] == "MSTKERR":
                diagnosis.append(
                    "Stack overflow during exception entry. Stack pointer has gone "
                    "below allocated stack region. Increase stack size or reduce usage."
                )
            elif fb["bit"] == "DIVBYZERO":
                diagnosis.append(
                    "Division by zero. Check the divisor variable at the faulting instruction."
                )
            elif fb["bit"] == "UNDEFINSTR":
                diagnosis.append(
                    "Undefined instruction. Possible causes: corrupted code memory, "
                    "jumped to data, or missing instruction set extension."
                )
            elif fb["bit"] == "NOCP":
                diagnosis.append(
                    "Coprocessor access fault. If using FPU, ensure CPACR is configured: "
                    "SCB->CPACR |= (0xF << 20) to enable CP10/CP11."
                )

        if not diagnosis and hfsr & (1 << 30):
            diagnosis.append(
                "HardFault with FORCED bit but no specific CFSR bits set. "
                "Possible causes: faults disabled in SHCSR, or double fault."
            )

        report["diagnosis"] = diagnosis

        # If SVD is loaded, try to decode fault registers via SVD too
        if decoder:
            svd_decoded = {}
            for name, val in raw_regs.items():
                try:
                    dec = decoder.decode_address(FAULT_REGS[name], val)
                    if dec:
                        svd_decoded[name] = {
                            "fields": [
                                {"name": f.name, "value": f.value, "desc": f.description}
                                for f in dec.fields
                            ]
                        }
                except Exception:
                    pass
            if svd_decoded:
                report["svd_decoded_faults"] = svd_decoded

        return _format_fault_report(report)

    # ── Dashboard (SoftICE/IDA-style composite view) ─────────────────

    @mcp.tool()
    async def target_dashboard(
        session_id: str,
        style: str = "softice",
        code_lines: int = 16,
        stack_bytes: int = 64,
        data_address: str | None = None,
        data_length: int = 64,
    ):
        """SoftICE/IDA-style debug dashboard: registers, code, stack, backtrace in one view.

        Returns a composite debug snapshot showing everything at the current
        stop point. Call this after any stop event for full situational awareness.

        Args:
            session_id: Session ID
            style: "softice" (dense, max info per line) or "ida" (boxed panels, structured)
            code_lines: Number of disassembly lines around PC (default 16)
            data_address: Optional memory address to show in data window (hex string)
            data_length: Bytes for data window (default 64)
            stack_bytes: Bytes of stack to show (default 64)
        """
        session = _get_session(session_id)
        assert session.connection is not None

        # ── Registers ──
        regs = await session.read_registers()

        # ── Disassembly around PC ──
        pc_str = regs.get("pc", "0x0")
        pc_val = int(pc_str, 0)
        # Center on PC: show ~half before, ~half after
        # Thumb instructions are 2-4 bytes, ARM are 4 bytes; use 3 as avg
        half = code_lines // 2
        dis_start = max(0, pc_val - half * 3)
        dis_end = pc_val + half * 4

        disasm_lines: list[str] = []
        try:
            # Try mixed mode first (source + asm), fall back to plain asm
            try:
                result = await session.connection.send(
                    f'-data-disassemble -s 0x{dis_start:08x} -e 0x{dis_end:08x} -- 1'
                )
                asm_insns = result.results.get("asm_insns", [])
            except Exception:
                asm_insns = []
            if not asm_insns:
                result = await session.connection.send(
                    f'-data-disassemble -s 0x{dis_start:08x} -e 0x{dis_end:08x} -- 0'
                )
                asm_insns = result.results.get("asm_insns", [])

            # Parse mixed mode (source + asm)
            instructions: list[dict[str, str]] = []
            for entry in asm_insns:
                if not isinstance(entry, dict):
                    continue
                if "src_and_asm_line" in entry:
                    src_info = entry["src_and_asm_line"]
                    src_file = src_info.get("file", "")
                    src_line = src_info.get("line", "")
                    insns = entry.get("line_asm_insn", [])
                    for insn in insns:
                        if isinstance(insn, dict):
                            instructions.append({
                                "addr": insn.get("address", ""),
                                "inst": insn.get("inst", ""),
                                "func": insn.get("func-name", ""),
                                "offset": insn.get("offset", ""),
                                "src_file": src_file,
                                "src_line": src_line,
                            })
                elif "address" in entry:
                    instructions.append({
                        "addr": entry.get("address", ""),
                        "inst": entry.get("inst", ""),
                        "func": entry.get("func-name", ""),
                        "offset": entry.get("offset", ""),
                        "src_file": "",
                        "src_line": "",
                    })

            # Format with PC marker, source interleaving
            last_src: tuple[str, str] | None = None
            shown = 0
            for insn in instructions:
                if shown >= code_lines:
                    break
                addr = int(insn["addr"], 0) if insn["addr"] else 0
                src_key = (insn["src_file"], insn["src_line"])
                if src_key != last_src and insn["src_file"] and insn["src_line"]:
                    disasm_lines.append(f"      {insn['src_file']}:{insn['src_line']}")
                    last_src = src_key

                marker = " >" if addr == pc_val else "  "
                func_label = ""
                if insn["func"] and insn["offset"] == "0":
                    func_label = f"  <{insn['func']}>:"
                    disasm_lines.append(f"  {func_label}")

                inst_str = insn["inst"]
                pc_note = "  <<<" if addr == pc_val else ""
                disasm_lines.append(f"{marker} {addr:08X}  {inst_str}{pc_note}")
                shown += 1

        except Exception as exc:
            disasm_lines.append(f"  (disassembly error: {exc})")

        # ── Stack dump ──
        sp_val = int(regs.get("sp", "0x0"), 0)
        try:
            stack_data = await session.read_memory(sp_val, stack_bytes)
            stack_dump = _format_hex_dump(stack_data, sp_val)
        except Exception:
            stack_dump = "  (cannot read stack)"

        # ── Optional data window ──
        data_dump = None
        data_addr = None
        if data_address:
            data_addr = int(data_address, 0)
            try:
                data_bytes = await session.read_memory(data_addr, data_length)
                data_dump = _format_hex_dump(data_bytes, data_addr)
            except Exception:
                data_dump = "  (cannot read memory)"

        # ── Backtrace ──
        bt_lines: list[str] = []
        try:
            frames = await session.backtrace()
            for f in frames:
                level = f.get("level", "?")
                func = f.get("function", "??")
                file = f.get("file", "")
                line = f.get("line", "")
                addr = f.get("address", "")
                loc = f"  {file}:{line}" if file and line else ""
                bt_lines.append(f" #{level}  {func}{loc}  [{addr}]")
        except Exception:
            bt_lines.append(" (backtrace unavailable)")

        # ── Source context (try to get source listing) ──
        source_text = None
        try:
            output = await session.raw_command("list")
            if output.strip() and "No" not in output[:20]:
                source_text = output
        except Exception:
            pass

        return _format_dashboard(
            regs=regs,
            disasm_lines=disasm_lines,
            stack_dump=stack_dump,
            sp_val=sp_val,
            bt_lines=bt_lines,
            source_text=source_text,
            data_dump=data_dump,
            data_addr=data_addr,
            style=style,
        )

    return mcp
