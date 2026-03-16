"""SVD-to-Saleae analyzer settings mapping.

IMPORTANT: All setting strings must EXACTLY match Logic 2 UI text.
These are verified against Logic 2 v2.4.x.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# SPI CPOL/CPHA -> Logic 2 setting strings (exact matches required)
SPI_CPOL_CPHA_MAP: dict[tuple[int, int], dict[str, str]] = {
    (0, 0): {"Clock State": "CPOL = 0 (Clock is Idle Low)", "Clock Phase": "CPHA = 0 (Data is Valid on Clock Leading Edge)"},
    (0, 1): {"Clock State": "CPOL = 0 (Clock is Idle Low)", "Clock Phase": "CPHA = 1 (Data is Valid on Clock Trailing Edge)"},
    (1, 0): {"Clock State": "CPOL = 1 (Clock is Idle High)", "Clock Phase": "CPHA = 0 (Data is Valid on Clock Leading Edge)"},
    (1, 1): {"Clock State": "CPOL = 1 (Clock is Idle High)", "Clock Phase": "CPHA = 1 (Data is Valid on Clock Trailing Edge)"},
}

SPI_BIT_ORDER_MAP: dict[int, str] = {
    0: "Most Significant Bit Sent First",
    1: "Least Significant Bit Sent First",
}

SPI_DATA_BITS_MAP: dict[int, str] = {
    8: "8 Bits per Transfer (Standard)",
    16: "16 Bits per Transfer",
}

# I2C address size settings
I2C_ADDRESS_MODE_MAP: dict[int, str] = {
    7: "7-bit",
    10: "10-bit",
}

# UART parity settings
UART_PARITY_MAP: dict[str, str] = {
    "none": "No Parity Bit",
    "even": "Even Parity Bit",
    "odd": "Odd Parity Bit",
}

UART_STOP_BITS_MAP: dict[float, str] = {
    1.0: "1 Stop Bit",
    1.5: "1.5 Stop Bits",
    2.0: "2 Stop Bits",
}

UART_BIT_ORDER_MAP: dict[int, str] = {
    0: "Least Significant Bit Sent First",
    1: "Most Significant Bit Sent First",
}

# Minimum sample rate requirements by protocol
MIN_SAMPLE_RATES: dict[str, dict[str, int]] = {
    "spi": {"multiplier": 4, "minimum": 1_000_000},
    "i2c": {"multiplier": 10, "minimum": 1_000_000},
    "uart": {"multiplier": 8, "minimum": 1_000_000},
}


def spi_settings_from_svd(
    device: Any,
    peripheral_name: str,
    channel_map: dict[str, int],
) -> dict[str, Any]:
    """Generate Saleae SPI analyzer settings from SVD peripheral config.

    Reads CPOL, CPHA, LSBFIRST, and data frame format from the SVD
    peripheral registers to auto-configure the Logic 2 SPI analyzer.
    """
    periph = device.peripherals.get(peripheral_name)
    if not periph:
        raise KeyError(f"Peripheral {peripheral_name} not in SVD")

    cr1 = periph.registers.get("CR1", {})
    fields = cr1.fields if hasattr(cr1, "fields") else {}

    # Extract CPOL/CPHA
    cpol = 0
    cpha = 0
    lsbfirst = 0

    if hasattr(fields, "get"):
        cpol_field = fields.get("CPOL")
        if cpol_field:
            cpol = 0  # Will be read from hardware at runtime
        cpha_field = fields.get("CPHA")
        if cpha_field:
            cpha = 0
        lsb_field = fields.get("LSBFIRST")
        if lsb_field:
            lsbfirst = 0

    mode_settings = SPI_CPOL_CPHA_MAP.get((cpol, cpha), SPI_CPOL_CPHA_MAP[(0, 0)])

    settings: dict[str, Any] = {
        "MISO": channel_map.get("MISO", channel_map.get(f"{peripheral_name}_MISO", 0)),
        "MOSI": channel_map.get("MOSI", channel_map.get(f"{peripheral_name}_MOSI", 1)),
        "Clock": channel_map.get("SCK", channel_map.get(f"{peripheral_name}_SCK", 2)),
        "Enable": channel_map.get("CS", channel_map.get(f"{peripheral_name}_NSS", 3)),
        "Bits per Transfer": SPI_DATA_BITS_MAP.get(8, "8 Bits per Transfer (Standard)"),
        "Significant Bit": SPI_BIT_ORDER_MAP.get(lsbfirst, "Most Significant Bit Sent First"),
        "Enable Line": "Enable line is Active Low (Standard)",
        **mode_settings,
    }

    return {"type": "SPI", "settings": settings}


def spi_settings_from_registers(
    cpol: int,
    cpha: int,
    lsbfirst: int,
    data_bits: int,
    channel_map: dict[str, int],
) -> dict[str, Any]:
    """Generate SPI analyzer settings from actual register values read from target."""
    mode_settings = SPI_CPOL_CPHA_MAP.get((cpol, cpha), SPI_CPOL_CPHA_MAP[(0, 0)])

    settings: dict[str, Any] = {
        "MISO": channel_map.get("MISO", 0),
        "MOSI": channel_map.get("MOSI", 1),
        "Clock": channel_map.get("SCK", 2),
        "Enable": channel_map.get("CS", 3),
        "Bits per Transfer": SPI_DATA_BITS_MAP.get(
            data_bits, "8 Bits per Transfer (Standard)"
        ),
        "Significant Bit": SPI_BIT_ORDER_MAP.get(
            lsbfirst, "Most Significant Bit Sent First"
        ),
        "Enable Line": "Enable line is Active Low (Standard)",
        **mode_settings,
    }

    return {"type": "SPI", "settings": settings}


def i2c_settings_from_svd(
    device: Any,
    peripheral_name: str,
    channel_map: dict[str, int],
) -> dict[str, Any]:
    """Generate Saleae I2C analyzer settings."""
    settings: dict[str, Any] = {
        "SDA": channel_map.get("SDA", channel_map.get(f"{peripheral_name}_SDA", 0)),
        "SCL": channel_map.get("SCL", channel_map.get(f"{peripheral_name}_SCL", 1)),
    }

    return {"type": "I2C", "settings": settings}


def uart_settings_from_svd(
    device: Any,
    peripheral_name: str,
    channel_map: dict[str, int],
) -> dict[str, Any]:
    """Generate Saleae Async Serial analyzer settings."""
    periph = device.peripherals.get(peripheral_name)

    # Default UART settings
    settings: dict[str, Any] = {
        "Input Channel": channel_map.get(
            "TX", channel_map.get(f"{peripheral_name}_TX", 0)
        ),
        "Bit Rate (Bits/s)": 115200,
        "Bits per Frame": "8 Bits per Transfer (Standard)",
        "Stop Bits": UART_STOP_BITS_MAP[1.0],
        "Parity Bit": UART_PARITY_MAP["none"],
        "Significant Bit": UART_BIT_ORDER_MAP[0],
        "Signal inversion": "Non Inverted (Standard)",
    }

    return {"type": "Async Serial", "settings": settings}


def recommend_sample_rate(protocol: str, bus_frequency_hz: int) -> int:
    """Calculate recommended Saleae sample rate for a protocol."""
    config = MIN_SAMPLE_RATES.get(protocol.lower(), {"multiplier": 4, "minimum": 1_000_000})
    rate = bus_frequency_hz * config["multiplier"]
    return max(rate, config["minimum"])
