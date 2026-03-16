"""SVD XML parser with caching and lazy peripheral loading."""

from __future__ import annotations

import copy
import logging
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MAX_CACHE = 3


def _text(el: ET.Element | None, tag: str, default: str = "") -> str:
    if el is None:
        return default
    child = el.find(tag)
    if child is None or child.text is None:
        return default
    return child.text.strip()


def _int(el: ET.Element | None, tag: str, default: int = 0) -> int:
    raw = _text(el, tag)
    if not raw:
        return default
    raw = raw.strip().lower()
    if raw.startswith("0x"):
        return int(raw, 16)
    if raw.startswith("#"):
        return int(raw[1:], 2)
    return int(raw)


@dataclass(frozen=True)
class SVDCpu:
    name: str = ""
    revision: str = ""
    endian: str = "little"
    nvic_priority_bits: int = 4


@dataclass(frozen=True)
class SVDField:
    name: str
    description: str
    bit_offset: int
    bit_width: int
    access: str = "read-write"
    enumerated_values: dict[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SVDRegister:
    name: str
    description: str
    address_offset: int
    size: int = 32
    reset_value: int = 0
    fields: dict[str, SVDField] = field(default_factory=dict)
    access: str = "read-write"


@dataclass
class SVDPeripheral:
    name: str
    description: str
    base_address: int
    group_name: str = ""
    derived_from: str | None = None
    _registers_xml: ET.Element | None = field(default=None, repr=False)
    _registers: dict[str, SVDRegister] | None = field(default=None, repr=False)
    _device_ref: SVDDevice | None = field(default=None, repr=False)

    @property
    def registers(self) -> dict[str, SVDRegister]:
        if self._registers is None:
            self._registers = self._parse_registers()
        return self._registers

    def _parse_registers(self) -> dict[str, SVDRegister]:
        if self._registers_xml is None:
            # Check if derived -- get parent registers
            if self.derived_from and self._device_ref:
                parent = self._device_ref.peripherals.get(self.derived_from)
                if parent:
                    return dict(parent.registers)
            return {}

        regs: dict[str, SVDRegister] = {}
        for reg_el in self._registers_xml.findall("register"):
            reg = _parse_register(reg_el)
            if reg:
                regs[reg.name] = reg

        # Handle register clusters
        for cluster_el in self._registers_xml.findall("cluster"):
            cluster_name = _text(cluster_el, "name")
            cluster_offset = _int(cluster_el, "addressOffset")
            for reg_el in cluster_el.findall("register"):
                reg = _parse_register(reg_el, cluster_offset, cluster_name)
                if reg:
                    regs[reg.name] = reg

        return regs


@dataclass
class SVDDevice:
    name: str
    description: str = ""
    peripherals: dict[str, SVDPeripheral] = field(default_factory=dict)
    cpu: SVDCpu = field(default_factory=SVDCpu)


def _parse_register(
    el: ET.Element,
    base_offset: int = 0,
    cluster_prefix: str = "",
) -> SVDRegister | None:
    name = _text(el, "name")
    if not name:
        return None

    if cluster_prefix:
        name = f"{cluster_prefix}_{name}"

    fields: dict[str, SVDField] = {}
    fields_el = el.find("fields")
    if fields_el is not None:
        for field_el in fields_el.findall("field"):
            f = _parse_field(field_el)
            if f:
                fields[f.name] = f

    return SVDRegister(
        name=name,
        description=_text(el, "description"),
        address_offset=base_offset + _int(el, "addressOffset"),
        size=_int(el, "size", 32),
        reset_value=_int(el, "resetValue"),
        fields=fields,
        access=_text(el, "access", "read-write"),
    )


def _parse_field(el: ET.Element) -> SVDField | None:
    name = _text(el, "name")
    if not name:
        return None

    # Bit range can be specified multiple ways
    bit_offset = _int(el, "bitOffset", -1)
    bit_width = _int(el, "bitWidth", -1)

    if bit_offset < 0 or bit_width < 0:
        lsb = _int(el, "lsb", -1)
        msb = _int(el, "msb", -1)
        if lsb >= 0 and msb >= 0:
            bit_offset = lsb
            bit_width = msb - lsb + 1

    if bit_offset < 0 or bit_width < 0:
        bit_range = _text(el, "bitRange")
        if bit_range:
            # Format: [MSB:LSB]
            bit_range = bit_range.strip("[]")
            parts = bit_range.split(":")
            if len(parts) == 2:
                msb = int(parts[0])
                lsb = int(parts[1])
                bit_offset = lsb
                bit_width = msb - lsb + 1

    if bit_offset < 0:
        bit_offset = 0
    if bit_width < 0:
        bit_width = 1

    # Parse enumerated values
    enums: dict[int, str] = {}
    for ev_el in el.findall(".//enumeratedValue"):
        ev_name = _text(ev_el, "name")
        ev_val_str = _text(ev_el, "value")
        if ev_name and ev_val_str:
            try:
                ev_val_str = ev_val_str.strip().lower()
                if ev_val_str.startswith("0x"):
                    ev_val = int(ev_val_str, 16)
                elif ev_val_str.startswith("#"):
                    ev_val = int(ev_val_str[1:], 2)
                else:
                    ev_val = int(ev_val_str)
                enums[ev_val] = ev_name
            except ValueError:
                pass

    return SVDField(
        name=name,
        description=_text(el, "description"),
        bit_offset=bit_offset,
        bit_width=bit_width,
        access=_text(el, "access", "read-write"),
        enumerated_values=enums,
    )


class SVDParser:
    """Parses SVD XML files with LRU caching and lazy register loading."""

    def __init__(self) -> None:
        self._cache: OrderedDict[str, SVDDevice] = OrderedDict()

    def load(self, svd_path: str) -> SVDDevice:
        path = str(Path(svd_path).resolve())
        if path in self._cache:
            self._cache.move_to_end(path)
            return self._cache[path]

        tree = ET.parse(path)
        root = tree.getroot()

        # Parse CPU info
        cpu_el = root.find("cpu")
        cpu = SVDCpu(
            name=_text(cpu_el, "name"),
            revision=_text(cpu_el, "revision"),
            endian=_text(cpu_el, "endian", "little"),
            nvic_priority_bits=_int(cpu_el, "nvicPrioBits", 4),
        )

        device = SVDDevice(
            name=_text(root, "name"),
            description=_text(root, "description"),
            cpu=cpu,
        )

        # First pass: collect all peripherals with metadata only
        peripherals_el = root.find("peripherals")
        if peripherals_el is not None:
            for periph_el in peripherals_el.findall("peripheral"):
                name = _text(periph_el, "name")
                if not name:
                    continue

                derived_from = periph_el.get("derivedFrom")
                registers_el = periph_el.find("registers")

                periph = SVDPeripheral(
                    name=name,
                    description=_text(periph_el, "description"),
                    base_address=_int(periph_el, "baseAddress"),
                    group_name=_text(periph_el, "groupName"),
                    derived_from=derived_from,
                    _registers_xml=registers_el,
                    _device_ref=device,
                )
                device.peripherals[name] = periph

        # Resolve derived peripherals (handle chains with cycle detection)
        self._resolve_derived(device)

        # Cache with eviction
        self._cache[path] = device
        while len(self._cache) > _MAX_CACHE:
            self._cache.popitem(last=False)

        return device

    def load_for_chip(self, chip_name: str) -> SVDDevice | None:
        """Try to find and load SVD for a chip name. Returns None if not found."""
        from .registry import SVDRegistry

        registry = SVDRegistry()
        path = registry.find(chip_name)
        if path:
            return self.load(path)
        return None

    def _resolve_derived(self, device: SVDDevice) -> None:
        """Resolve derivedFrom chains with cycle detection."""
        resolved: set[str] = set()

        def resolve(name: str, visited: set[str]) -> None:
            if name in resolved or name not in device.peripherals:
                return
            periph = device.peripherals[name]
            if periph.derived_from is None:
                resolved.add(name)
                return
            if periph.derived_from in visited:
                logger.warning("Cycle in derivedFrom chain: %s", visited)
                resolved.add(name)
                return

            visited.add(name)
            resolve(periph.derived_from, visited)
            resolved.add(name)

        for name in device.peripherals:
            resolve(name, set())
