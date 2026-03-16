"""Saleae Logic 2 automation API connection manager."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class SaleaeConnectionError(Exception):
    pass


class SaleaeConnection:
    """Lazy connection to Logic 2 automation API."""

    def __init__(self, host: str = "127.0.0.1", port: int = 10430) -> None:
        self._host = host
        self._port = port
        self._manager: Any = None  # saleae.automation.Manager
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self, force: bool = False) -> dict[str, Any]:
        """Connect to Logic 2. Raises if not available."""
        if self._connected and not force:
            return self.get_device_info()

        # Check for headless environment
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            raise SaleaeConnectionError(
                "No display detected. Logic 2 requires a GUI. "
                "On headless Linux, install XVFB: "
                "sudo apt install xvfb && xvfb-run logic2"
            )

        try:
            from saleae.automation import Manager  # type: ignore[import-untyped]
        except ImportError:
            raise SaleaeConnectionError(
                "logic2-automation not installed. "
                "Install with: pip install 'claude-jtag-debug-server[saleae]'"
            )

        if self._manager and force:
            try:
                self._manager.close()
            except Exception:
                pass

        try:
            self._manager = Manager.connect(
                address=self._host,
                port=self._port,
            )
            self._connected = True
        except Exception as exc:
            self._connected = False
            error_msg = str(exc)
            if "Connection refused" in error_msg:
                raise SaleaeConnectionError(
                    "Cannot connect to Logic 2 automation API. "
                    "Ensure Logic 2 is running and automation is enabled: "
                    "Settings > Preferences > Enable Automation (bottom of page). "
                    f"Expected at {self._host}:{self._port}."
                ) from exc
            if "already connected" in error_msg.lower():
                raise SaleaeConnectionError(
                    "Another automation client is connected to Logic 2. "
                    "Only one client is allowed. Close the other client or "
                    "call with force=True to attempt reconnect."
                ) from exc
            raise SaleaeConnectionError(f"Failed to connect: {exc}") from exc

        return self.get_device_info()

    async def disconnect(self) -> None:
        if self._manager:
            try:
                self._manager.close()
            except Exception:
                pass
        self._manager = None
        self._connected = False

    def get_device_info(self) -> dict[str, Any]:
        if not self._connected or not self._manager:
            return {"connected": False}

        try:
            devices = self._manager.get_devices()
            if devices:
                dev = devices[0]
                return {
                    "connected": True,
                    "device_type": str(getattr(dev, "device_type", "unknown")),
                    "device_id": str(getattr(dev, "device_id", "")),
                    "host": self._host,
                    "port": self._port,
                }
            return {"connected": True, "device_type": "none", "host": self._host}
        except Exception as exc:
            logger.warning("Error getting device info: %s", exc)
            self._connected = False
            return {"connected": False, "error": str(exc)}

    @property
    def manager(self) -> Any:
        if not self._connected or not self._manager:
            raise SaleaeConnectionError("Not connected to Logic 2")
        return self._manager
