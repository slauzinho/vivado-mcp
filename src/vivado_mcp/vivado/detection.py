"""Vivado installation detection module.

This module provides functionality to automatically detect Vivado installations
on the system, supporting both Windows and Linux platforms.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class VivadoInstallation:
    """Represents a detected Vivado installation."""

    version: str
    path: Path
    executable: Path

    def to_dict(self) -> dict[str, str]:
        """Convert to dictionary for JSON serialization."""
        return {
            "version": self.version,
            "path": str(self.path),
            "executable": str(self.executable),
        }


def _parse_version(version_str: str) -> tuple[int, ...]:
    """Parse a version string into a tuple for comparison.

    Args:
        version_str: Version string like "2023.2" or "2024.1.1"

    Returns:
        Tuple of version numbers for comparison
    """
    parts = re.split(r"[._-]", version_str)
    result: list[int] = []
    for part in parts:
        try:
            result.append(int(part))
        except ValueError:
            break
    return tuple(result)


def _get_windows_search_paths() -> list[Path]:
    """Get standard Vivado installation paths on Windows."""
    paths: list[Path] = []

    # Standard Xilinx installation locations
    for drive in ["C:", "D:", "E:"]:
        paths.append(Path(f"{drive}/Xilinx/Vivado"))
        paths.append(Path(f"{drive}/Xilinx/Vivado_Lab"))

    # Also check Program Files
    program_files = os.environ.get("PROGRAMFILES", "C:/Program Files")
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")

    paths.append(Path(program_files) / "Xilinx" / "Vivado")
    paths.append(Path(program_files_x86) / "Xilinx" / "Vivado")

    return paths


def _get_linux_search_paths() -> list[Path]:
    """Get standard Vivado installation paths on Linux."""
    paths: list[Path] = []

    # Standard Xilinx installation locations on Linux
    paths.append(Path("/opt/Xilinx/Vivado"))
    paths.append(Path("/tools/Xilinx/Vivado"))

    # Home directory installations
    home = Path.home()
    paths.append(home / "Xilinx" / "Vivado")
    paths.append(home / ".Xilinx" / "Vivado")

    return paths


def _get_search_paths() -> list[Path]:
    """Get platform-appropriate search paths for Vivado installations."""
    if os.name == "nt":
        return _get_windows_search_paths()
    return _get_linux_search_paths()


def _find_vivado_executable(version_path: Path) -> Path | None:
    """Find the Vivado executable within a version directory.

    Args:
        version_path: Path to a Vivado version directory (e.g., C:/Xilinx/Vivado/2023.2)

    Returns:
        Path to the vivado executable, or None if not found
    """
    if os.name == "nt":
        # Windows: look for vivado.bat in bin directory
        executable = version_path / "bin" / "vivado.bat"
        if executable.exists():
            return executable
        # Also check for vivado.exe
        executable = version_path / "bin" / "vivado.exe"
        if executable.exists():
            return executable
    else:
        # Linux: look for vivado script in bin directory
        executable = version_path / "bin" / "vivado"
        if executable.exists():
            return executable

    return None


def _is_valid_version_dir(path: Path) -> bool:
    """Check if a directory name looks like a Vivado version."""
    name = path.name
    # Vivado versions are typically like 2023.2, 2024.1, etc.
    return bool(re.match(r"^\d{4}\.\d", name))


def detect_vivado_installations(
    search_paths: list[Path] | None = None,
) -> list[VivadoInstallation]:
    """Detect all Vivado installations on the system.

    Args:
        search_paths: Optional list of paths to search. If None, uses platform defaults.

    Returns:
        List of detected VivadoInstallation objects, sorted by version (newest first)
    """
    if search_paths is None:
        search_paths = _get_search_paths()

    installations: list[VivadoInstallation] = []
    seen_paths: set[Path] = set()

    for base_path in search_paths:
        if not base_path.exists() or not base_path.is_dir():
            continue

        # Look for version directories within the base path
        try:
            for version_dir in base_path.iterdir():
                if not version_dir.is_dir():
                    continue

                # Skip if not a valid version directory name
                if not _is_valid_version_dir(version_dir):
                    continue

                # Resolve to handle symlinks and normalize path
                resolved_path = version_dir.resolve()
                if resolved_path in seen_paths:
                    continue
                seen_paths.add(resolved_path)

                # Find the executable
                executable = _find_vivado_executable(version_dir)
                if executable is None:
                    continue

                installations.append(
                    VivadoInstallation(
                        version=version_dir.name,
                        path=version_dir,
                        executable=executable,
                    )
                )
        except PermissionError:
            # Skip directories we can't read
            continue

    # Sort by version, newest first
    installations.sort(key=lambda x: _parse_version(x.version), reverse=True)

    return installations


def get_default_vivado(
    override_path: Path | None = None,
    override_version: str | None = None,
) -> VivadoInstallation | None:
    """Get the default Vivado installation to use.

    The selection priority is:
    1. If override_path is provided, use that specific installation
    2. If override_version is provided, find that version from detected installations
    3. Otherwise, use the most recent detected version

    Args:
        override_path: Optional explicit path to a Vivado installation
        override_version: Optional specific version to use (e.g., "2023.2")

    Returns:
        The selected VivadoInstallation, or None if no installation found
    """
    # If an explicit path is provided, use it directly
    if override_path is not None:
        override_path = Path(override_path)
        if not override_path.exists():
            return None

        executable = _find_vivado_executable(override_path)
        if executable is None:
            return None

        # Extract version from path name
        version = override_path.name
        if not _is_valid_version_dir(override_path):
            # Try to extract from parent if this is a bin directory or similar
            version = "unknown"

        return VivadoInstallation(
            version=version,
            path=override_path,
            executable=executable,
        )

    # Detect all installations
    installations = detect_vivado_installations()

    if not installations:
        return None

    # If a specific version is requested, find it
    if override_version is not None:
        for install in installations:
            if install.version == override_version:
                return install
        # Version not found
        return None

    # Return the most recent version (first in sorted list)
    return installations[0]
