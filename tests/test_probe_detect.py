"""Tests for probe detection."""

import pytest
from unittest.mock import AsyncMock, patch

from server.probe.detect import detect_probes, KNOWN_PROBES, ProbeInfo


class TestKnownProbes:
    def test_stlink_v2_known(self):
        assert (0x0483, 0x3748) in KNOWN_PROBES

    def test_jlink_known(self):
        assert (0x1366, 0x0101) in KNOWN_PROBES

    def test_cmsis_dap_known(self):
        assert (0x0D28, 0x0204) in KNOWN_PROBES

    def test_probe_info_dataclass(self):
        info = ProbeInfo(name="Test", probe_type="stlink", vid=0x0483, pid=0x3748)
        assert info.name == "Test"
        assert info.probe_type == "stlink"


class TestLsusbParsing:
    @pytest.mark.asyncio
    async def test_detect_stlink_from_lsusb(self):
        mock_output = (
            b"Bus 001 Device 003: ID 0483:3748 STMicroelectronics ST-LINK/V2\n"
            b"Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub\n"
        )

        with patch("server.probe.detect.asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(mock_output, b""))
            mock_exec.return_value = mock_proc

            probes = await detect_probes()
            assert len(probes) == 1
            assert probes[0].name == "ST-Link V2"
            assert probes[0].probe_type == "stlink"

    @pytest.mark.asyncio
    async def test_detect_multiple_probes(self):
        mock_output = (
            b"Bus 001 Device 003: ID 0483:3748 STMicroelectronics ST-LINK/V2\n"
            b"Bus 001 Device 004: ID 1366:0101 SEGGER J-Link\n"
        )

        with patch("server.probe.detect.asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(mock_output, b""))
            mock_exec.return_value = mock_proc

            probes = await detect_probes()
            assert len(probes) == 2

    @pytest.mark.asyncio
    async def test_no_probes_found(self):
        mock_output = b"Bus 001 Device 001: ID 1d6b:0002 Linux Foundation 2.0 root hub\n"

        with patch("server.probe.detect.asyncio.create_subprocess_exec") as mock_exec:
            mock_proc = AsyncMock()
            mock_proc.communicate = AsyncMock(return_value=(mock_output, b""))
            mock_exec.return_value = mock_proc

            probes = await detect_probes()
            assert len(probes) == 0
