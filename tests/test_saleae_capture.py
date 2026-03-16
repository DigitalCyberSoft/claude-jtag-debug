"""Tests for Saleae capture coordination and analyzer config."""

import pytest

from server.saleae.analyzer_config import (
    SPI_CPOL_CPHA_MAP,
    SPI_BIT_ORDER_MAP,
    spi_settings_from_registers,
    recommend_sample_rate,
)
from server.saleae.capture import CaptureCoordinator, CaptureResult, AnalyzerResult
from server.saleae.connection import SaleaeConnection


class TestAnalyzerConfig:
    def test_all_spi_modes_mapped(self):
        assert (0, 0) in SPI_CPOL_CPHA_MAP
        assert (0, 1) in SPI_CPOL_CPHA_MAP
        assert (1, 0) in SPI_CPOL_CPHA_MAP
        assert (1, 1) in SPI_CPOL_CPHA_MAP

    def test_spi_mode0_settings(self):
        settings = SPI_CPOL_CPHA_MAP[(0, 0)]
        assert "CPOL = 0" in settings["Clock State"]
        assert "CPHA = 0" in settings["Clock Phase"]

    def test_spi_mode3_settings(self):
        settings = SPI_CPOL_CPHA_MAP[(1, 1)]
        assert "CPOL = 1" in settings["Clock State"]
        assert "CPHA = 1" in settings["Clock Phase"]

    def test_bit_order_msb(self):
        assert "Most Significant" in SPI_BIT_ORDER_MAP[0]

    def test_bit_order_lsb(self):
        assert "Least Significant" in SPI_BIT_ORDER_MAP[1]

    def test_spi_from_registers_mode0(self):
        channel_map = {"MISO": 0, "MOSI": 1, "SCK": 2, "CS": 3}
        config = spi_settings_from_registers(
            cpol=0, cpha=0, lsbfirst=0, data_bits=8, channel_map=channel_map
        )
        assert config["type"] == "SPI"
        assert config["settings"]["MISO"] == 0
        assert config["settings"]["Clock"] == 2
        assert "CPOL = 0" in config["settings"]["Clock State"]

    def test_spi_from_registers_mode3_lsb(self):
        channel_map = {"MISO": 4, "MOSI": 5, "SCK": 6, "CS": 7}
        config = spi_settings_from_registers(
            cpol=1, cpha=1, lsbfirst=1, data_bits=16, channel_map=channel_map
        )
        assert "CPOL = 1" in config["settings"]["Clock State"]
        assert "CPHA = 1" in config["settings"]["Clock Phase"]
        assert "Least Significant" in config["settings"]["Significant Bit"]


class TestSampleRateRecommendation:
    def test_spi_1mhz(self):
        rate = recommend_sample_rate("spi", 1_000_000)
        assert rate >= 4_000_000

    def test_spi_10mhz(self):
        rate = recommend_sample_rate("spi", 10_000_000)
        assert rate >= 40_000_000

    def test_i2c_100khz(self):
        rate = recommend_sample_rate("i2c", 100_000)
        assert rate >= 1_000_000

    def test_uart_115200(self):
        rate = recommend_sample_rate("uart", 115200)
        assert rate >= 921_600

    def test_minimum_floor(self):
        rate = recommend_sample_rate("uart", 1000)
        assert rate >= 1_000_000


class TestCaptureResult:
    def test_empty_result(self):
        r = CaptureResult()
        assert r.duration_seconds == 0.0
        assert r.analyzer_results == []
        assert r.error is None

    def test_error_result(self):
        r = CaptureResult(error="Connection lost")
        assert r.error == "Connection lost"

    def test_partial_result(self):
        r = CaptureResult(partial=True, error="Timeout")
        assert r.partial is True


class TestAnalyzerResult:
    def test_empty_analyzer(self):
        r = AnalyzerResult(analyzer_type="SPI", decoded_frames=[])
        assert r.frame_count == 0
        assert r.error_count == 0

    def test_truncated_flag(self):
        r = AnalyzerResult(
            analyzer_type="SPI",
            decoded_frames=[{"data": "0xFF"}] * 500,
            frame_count=500,
            truncated=True,
        )
        assert r.truncated is True


class TestCaptureCoordinator:
    def test_configure_channels(self):
        conn = SaleaeConnection()
        coord = CaptureCoordinator(conn)
        result = coord.configure_channels(
            {"SCK": 0, "MOSI": 1}, sample_rate=10_000_000
        )
        assert result["channel_map"] == {"SCK": 0, "MOSI": 1}
        assert result["sample_rate"] == 10_000_000
