"""Register value decoder using SVD definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .parser import SVDDevice, SVDField, SVDPeripheral, SVDRegister


@dataclass
class FieldDecode:
    name: str
    value: int
    description: str
    enumerated_name: str | None
    bit_range: str  # e.g. "[7:4]"


@dataclass
class RegisterDecode:
    peripheral: str
    register: str
    address: int
    raw_value: int
    fields: list[FieldDecode] = field(default_factory=list)


class RegisterDecoder:
    """Decodes raw register values into named bitfields using SVD data."""

    def __init__(self, device: SVDDevice) -> None:
        self._device = device
        self._addr_map: dict[int, tuple[str, str]] | None = None

    def decode_register(
        self,
        peripheral_name: str,
        register_name: str,
        raw_value: int,
    ) -> RegisterDecode:
        periph = self._device.peripherals.get(peripheral_name)
        if not periph:
            raise KeyError(f"Unknown peripheral: {peripheral_name}")

        reg = periph.registers.get(register_name)
        if not reg:
            raise KeyError(
                f"Unknown register: {peripheral_name}.{register_name}"
            )

        address = periph.base_address + reg.address_offset
        return self._decode(periph.name, reg, address, raw_value)

    def decode_address(self, address: int, raw_value: int) -> RegisterDecode | None:
        """Decode a register value by its absolute memory address."""
        if self._addr_map is None:
            self._build_addr_map()

        assert self._addr_map is not None
        entry = self._addr_map.get(address)
        if not entry:
            return None

        periph_name, reg_name = entry
        return self.decode_register(periph_name, reg_name, raw_value)

    def encode_field_value(
        self,
        peripheral_name: str,
        register_name: str,
        field_name: str,
        value: int,
        current_value: int,
    ) -> int:
        """Read-modify-write: update a single field in a register value.

        Returns the new full register value with only the specified field changed.
        """
        periph = self._device.peripherals.get(peripheral_name)
        if not periph:
            raise KeyError(f"Unknown peripheral: {peripheral_name}")

        reg = periph.registers.get(register_name)
        if not reg:
            raise KeyError(f"Unknown register: {register_name}")

        fld = reg.fields.get(field_name)
        if not fld:
            raise KeyError(f"Unknown field: {field_name}")

        mask = ((1 << fld.bit_width) - 1) << fld.bit_offset
        # Clear the field bits, then set new value
        new_value = (current_value & ~mask) | ((value << fld.bit_offset) & mask)
        return new_value

    def list_peripherals(self) -> list[str]:
        return sorted(self._device.peripherals.keys())

    def list_registers(self, peripheral_name: str) -> list[str]:
        periph = self._device.peripherals.get(peripheral_name)
        if not periph:
            raise KeyError(f"Unknown peripheral: {peripheral_name}")
        return sorted(periph.registers.keys())

    def get_register_address(self, peripheral_name: str, register_name: str) -> int:
        periph = self._device.peripherals.get(peripheral_name)
        if not periph:
            raise KeyError(f"Unknown peripheral: {peripheral_name}")
        reg = periph.registers.get(register_name)
        if not reg:
            raise KeyError(f"Unknown register: {register_name}")
        return periph.base_address + reg.address_offset

    def _decode(
        self,
        periph_name: str,
        reg: SVDRegister,
        address: int,
        raw_value: int,
    ) -> RegisterDecode:
        fields: list[FieldDecode] = []
        for fld in sorted(reg.fields.values(), key=lambda f: f.bit_offset):
            mask = (1 << fld.bit_width) - 1
            fval = (raw_value >> fld.bit_offset) & mask

            msb = fld.bit_offset + fld.bit_width - 1
            if fld.bit_width == 1:
                bit_range = f"[{fld.bit_offset}]"
            else:
                bit_range = f"[{msb}:{fld.bit_offset}]"

            enum_name = fld.enumerated_values.get(fval)

            fields.append(
                FieldDecode(
                    name=fld.name,
                    value=fval,
                    description=fld.description,
                    enumerated_name=enum_name,
                    bit_range=bit_range,
                )
            )

        return RegisterDecode(
            peripheral=periph_name,
            register=reg.name,
            address=address,
            raw_value=raw_value,
            fields=fields,
        )

    def _build_addr_map(self) -> None:
        self._addr_map = {}
        for periph in self._device.peripherals.values():
            for reg in periph.registers.values():
                addr = periph.base_address + reg.address_offset
                self._addr_map[addr] = (periph.name, reg.name)
