"""SVD file discovery across common locations."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SYSTEM_PATHS = [
    Path("/usr/share/cmsis-svd"),
    Path("/usr/local/share/cmsis-svd"),
    Path.home() / ".local" / "share" / "cmsis-svd",
]


class SVDRegistry:
    """Finds SVD files by chip name across known locations."""

    def __init__(self, extra_paths: list[str] | None = None) -> None:
        self._search_paths: list[Path] = []
        if extra_paths:
            self._search_paths.extend(Path(p) for p in extra_paths)
        self._search_paths.extend(_SYSTEM_PATHS)

        # Try cmsis-svd-data package
        try:
            import cmsis_svd_data  # type: ignore[import-untyped]

            pkg_dir = Path(cmsis_svd_data.__file__).parent / "data"
            if pkg_dir.is_dir():
                self._search_paths.insert(0, pkg_dir)
        except ImportError:
            pass

    def find(self, chip_name: str) -> str | None:
        """Find SVD file for a chip. Returns path or None."""
        chip_upper = chip_name.upper()
        chip_lower = chip_name.lower()

        for base in self._search_paths:
            if not base.is_dir():
                continue

            # Direct match
            for ext in (".svd", ".SVD"):
                candidate = base / f"{chip_name}{ext}"
                if candidate.is_file():
                    return str(candidate)

            # Case-insensitive recursive search
            for svd_file in base.rglob("*.svd"):
                stem = svd_file.stem.upper()
                if stem == chip_upper:
                    return str(svd_file)
                # Partial match: STM32F407xx matches STM32F407VG
                if chip_upper.startswith(stem.rstrip("Xx")):
                    return str(svd_file)
                if stem.startswith(chip_upper):
                    return str(svd_file)

        logger.info(
            "SVD not found for %s. Install cmsis-svd-data or provide --svd-path.",
            chip_name,
        )
        return None

    def list_available(self) -> list[str]:
        """List all available SVD files."""
        results: list[str] = []
        for base in self._search_paths:
            if not base.is_dir():
                continue
            for svd_file in base.rglob("*.svd"):
                results.append(str(svd_file))
        return sorted(results)
