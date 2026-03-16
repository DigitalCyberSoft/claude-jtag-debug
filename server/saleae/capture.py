"""Capture coordination between Saleae Logic 2 and GDB debug sessions."""

from __future__ import annotations

import asyncio
import csv
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .connection import SaleaeConnection, SaleaeConnectionError

logger = logging.getLogger(__name__)

_MAX_FRAMES = 500
_DEFAULT_CAPTURE_TIMEOUT = 30.0


@dataclass
class AnalyzerResult:
    analyzer_type: str
    decoded_frames: list[dict[str, Any]]
    error_count: int = 0
    frame_count: int = 0
    truncated: bool = False


@dataclass
class CaptureResult:
    duration_seconds: float = 0.0
    sample_rate: int = 0
    analyzer_results: list[AnalyzerResult] = field(default_factory=list)
    error: str | None = None
    partial: bool = False


class CaptureCoordinator:
    """Coordinates Saleae captures with GDB debug session events."""

    def __init__(self, saleae: SaleaeConnection) -> None:
        self._saleae = saleae
        self._channel_map: dict[str, int] = {}
        self._sample_rate: int = 25_000_000
        self._voltage: float = 3.3
        self._current_capture: Any = None
        self._analyzers: list[Any] = []

    def configure_channels(
        self,
        channel_map: dict[str, int],
        sample_rate: int = 25_000_000,
        voltage: float = 3.3,
    ) -> dict[str, Any]:
        """Set channel mapping and capture parameters."""
        self._channel_map = channel_map
        self._sample_rate = sample_rate
        self._voltage = voltage
        return {
            "channel_map": channel_map,
            "sample_rate": sample_rate,
            "voltage": voltage,
        }

    async def capture_timed(
        self,
        duration: float = 0.1,
        analyzers: list[dict[str, Any]] | None = None,
    ) -> CaptureResult:
        """Run a timed capture."""
        if not self._saleae.is_connected:
            raise SaleaeConnectionError("Not connected to Logic 2")

        try:
            from saleae.automation import (  # type: ignore[import-untyped]
                LogicDeviceConfiguration,
                CaptureConfiguration,
                TimedCaptureMode,
            )
        except ImportError:
            raise SaleaeConnectionError("logic2-automation not installed")

        digital_channels = list(set(self._channel_map.values()))

        device_config = LogicDeviceConfiguration(
            enabled_digital_channels=digital_channels,
            digital_sample_rate=self._sample_rate,
            digital_threshold_volts=self._voltage,
        )

        capture_config = CaptureConfiguration(
            capture_mode=TimedCaptureMode(duration_seconds=duration),
        )

        try:
            capture = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._saleae.manager.start_capture(
                    device_configuration=device_config,
                    capture_configuration=capture_config,
                ),
            )
            self._current_capture = capture

            # Wait for capture to complete
            await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, capture.wait),
                timeout=duration + _DEFAULT_CAPTURE_TIMEOUT,
            )

            result = CaptureResult(
                duration_seconds=duration,
                sample_rate=self._sample_rate,
            )

            # Add and extract analyzers
            if analyzers:
                result.analyzer_results = await self._extract_analyzer_data(
                    capture, analyzers
                )

            return result

        except asyncio.TimeoutError:
            if self._current_capture:
                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None, self._current_capture.stop
                    )
                except Exception:
                    pass
            return CaptureResult(
                duration_seconds=duration,
                sample_rate=self._sample_rate,
                error=f"Capture timed out after {duration + _DEFAULT_CAPTURE_TIMEOUT}s",
                partial=True,
            )
        except Exception as exc:
            return CaptureResult(error=str(exc))

    async def capture_triggered(
        self,
        trigger_channel: int,
        edge: str = "rising",
        pre_trigger: float = 0.001,
        post_trigger: float = 0.01,
        analyzers: list[dict[str, Any]] | None = None,
    ) -> CaptureResult:
        """Capture with digital trigger."""
        if not self._saleae.is_connected:
            raise SaleaeConnectionError("Not connected to Logic 2")

        try:
            from saleae.automation import (  # type: ignore[import-untyped]
                LogicDeviceConfiguration,
                CaptureConfiguration,
                DigitalTriggerCaptureMode,
                DigitalTriggerType,
            )
        except ImportError:
            raise SaleaeConnectionError("logic2-automation not installed")

        digital_channels = list(set(self._channel_map.values()))
        if trigger_channel not in digital_channels:
            digital_channels.append(trigger_channel)

        trigger_type = (
            DigitalTriggerType.RISING
            if edge.lower() == "rising"
            else DigitalTriggerType.FALLING
        )

        device_config = LogicDeviceConfiguration(
            enabled_digital_channels=digital_channels,
            digital_sample_rate=self._sample_rate,
            digital_threshold_volts=self._voltage,
        )

        capture_config = CaptureConfiguration(
            capture_mode=DigitalTriggerCaptureMode(
                trigger_channel_index=trigger_channel,
                trigger_type=trigger_type,
                min_pulse_width_seconds=None,
                max_pulse_width_seconds=None,
                after_trigger_seconds=post_trigger,
            ),
        )

        try:
            capture = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._saleae.manager.start_capture(
                    device_configuration=device_config,
                    capture_configuration=capture_config,
                ),
            )
            self._current_capture = capture

            timeout = pre_trigger + post_trigger + _DEFAULT_CAPTURE_TIMEOUT
            await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, capture.wait),
                timeout=timeout,
            )

            result = CaptureResult(
                duration_seconds=pre_trigger + post_trigger,
                sample_rate=self._sample_rate,
            )

            if analyzers:
                result.analyzer_results = await self._extract_analyzer_data(
                    capture, analyzers
                )

            return result

        except asyncio.TimeoutError:
            return CaptureResult(
                error="Trigger never fired. Check channel and target execution.",
                partial=True,
            )
        except Exception as exc:
            return CaptureResult(error=str(exc))

    async def capture_around_breakpoint(
        self,
        debug_session: Any,  # DebugSession -- avoid circular import
        breakpoint_location: str,
        pre_seconds: float = 0.001,
        post_seconds: float = 0.01,
        analyzers: list[dict[str, Any]] | None = None,
    ) -> CaptureResult:
        """The key closed-loop method: capture bus traffic around a breakpoint hit.

        Sequence:
        1. Set breakpoint at location
        2. Start capture in MANUAL mode (already recording)
        3. Resume target
        4. Wait for stop (breakpoint hit)
        5. Wait post_seconds
        6. Stop capture
        7. Extract analyzer data
        """
        if not self._saleae.is_connected:
            raise SaleaeConnectionError("Not connected to Logic 2")

        try:
            from saleae.automation import (  # type: ignore[import-untyped]
                LogicDeviceConfiguration,
                CaptureConfiguration,
                ManualCaptureMode,
            )
        except ImportError:
            raise SaleaeConnectionError("logic2-automation not installed")

        digital_channels = list(set(self._channel_map.values()))
        device_config = LogicDeviceConfiguration(
            enabled_digital_channels=digital_channels,
            digital_sample_rate=self._sample_rate,
            digital_threshold_volts=self._voltage,
        )
        capture_config = CaptureConfiguration(
            capture_mode=ManualCaptureMode(),
        )

        # 1. Set breakpoint
        bp_info = await debug_session.set_breakpoint(breakpoint_location, temporary=True)

        try:
            # 2. Start capture BEFORE continuing (critical ordering)
            capture = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._saleae.manager.start_capture(
                    device_configuration=device_config,
                    capture_configuration=capture_config,
                ),
            )
            self._current_capture = capture

            # 3. Resume target
            stop_info = await debug_session.continue_execution(timeout=_DEFAULT_CAPTURE_TIMEOUT)

            # 4. Post-breakpoint dwell time
            await asyncio.sleep(post_seconds)

            # 5. Stop capture
            await asyncio.get_event_loop().run_in_executor(None, capture.stop)

            result = CaptureResult(
                duration_seconds=post_seconds,
                sample_rate=self._sample_rate,
            )

            # 6. Extract data
            if analyzers:
                result.analyzer_results = await self._extract_analyzer_data(
                    capture, analyzers
                )

            return result

        except TimeoutError:
            # Target didn't hit breakpoint
            if self._current_capture:
                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None, self._current_capture.stop
                    )
                except Exception:
                    pass
            return CaptureResult(
                error="Target did not hit breakpoint within timeout",
                partial=True,
            )
        except Exception as exc:
            if self._current_capture:
                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None, self._current_capture.stop
                    )
                except Exception:
                    pass
            return CaptureResult(error=str(exc))

    async def _extract_analyzer_data(
        self,
        capture: Any,
        analyzer_configs: list[dict[str, Any]],
    ) -> list[AnalyzerResult]:
        """Add analyzers to capture and export decoded data."""
        results: list[AnalyzerResult] = []

        for config in analyzer_configs:
            analyzer_type = config.get("type", "")
            settings = config.get("settings", {})

            try:
                analyzer = capture.add_analyzer(analyzer_type, settings=settings)

                # Export to temp CSV
                with tempfile.NamedTemporaryFile(
                    suffix=".csv", delete=False, mode="w"
                ) as tmp:
                    tmp_path = tmp.name

                try:
                    await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: capture.export_data_table(
                            filepath=tmp_path,
                            analyzers=[analyzer],
                        ),
                    )

                    frames = self._parse_csv(tmp_path)
                    truncated = len(frames) > _MAX_FRAMES
                    if truncated:
                        frames = frames[:_MAX_FRAMES]

                    error_count = sum(
                        1 for f in frames if f.get("error") or "error" in str(f).lower()
                    )

                    results.append(
                        AnalyzerResult(
                            analyzer_type=analyzer_type,
                            decoded_frames=frames,
                            error_count=error_count,
                            frame_count=len(frames),
                            truncated=truncated,
                        )
                    )

                finally:
                    Path(tmp_path).unlink(missing_ok=True)

            except Exception as exc:
                logger.warning("Analyzer %s failed: %s", analyzer_type, exc)
                results.append(
                    AnalyzerResult(
                        analyzer_type=analyzer_type,
                        decoded_frames=[],
                        error_count=1,
                        frame_count=0,
                    )
                )

        return results

    @staticmethod
    def _parse_csv(path: str) -> list[dict[str, str]]:
        """Parse Saleae CSV export into list of dicts."""
        frames: list[dict[str, str]] = []
        try:
            with open(path, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    frames.append(dict(row))
        except Exception as exc:
            logger.warning("CSV parse error: %s", exc)
        return frames
