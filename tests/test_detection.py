"""Tests for Vivado detection module."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from vivado_mcp.vivado.detection import (
    VivadoInstallation,
    _is_valid_version_dir,
    _parse_version,
    detect_vivado_installations,
    get_default_vivado,
)


def create_mock_vivado_executable(bin_dir: Path) -> Path:
    """Create a mock Vivado executable appropriate for the current platform.

    Args:
        bin_dir: The bin directory where the executable should be created

    Returns:
        Path to the created executable
    """
    if os.name == "nt":
        executable = bin_dir / "vivado.bat"
    else:
        executable = bin_dir / "vivado"
    executable.touch()
    return executable


class TestParseVersion:
    """Tests for version parsing."""

    def test_simple_version(self) -> None:
        assert _parse_version("2023.2") == (2023, 2)

    def test_three_part_version(self) -> None:
        assert _parse_version("2024.1.1") == (2024, 1, 1)

    def test_version_with_suffix(self) -> None:
        # Should stop at non-numeric parts
        assert _parse_version("2023.2_beta") == (2023, 2)

    def test_empty_version(self) -> None:
        assert _parse_version("") == ()


class TestIsValidVersionDir:
    """Tests for version directory validation."""

    def test_valid_versions(self) -> None:
        assert _is_valid_version_dir(Path("2023.2"))
        assert _is_valid_version_dir(Path("2024.1"))
        assert _is_valid_version_dir(Path("2019.1"))

    def test_invalid_versions(self) -> None:
        assert not _is_valid_version_dir(Path("bin"))
        assert not _is_valid_version_dir(Path("docs"))
        assert not _is_valid_version_dir(Path("latest"))
        assert not _is_valid_version_dir(Path("v2023"))


class TestVivadoInstallation:
    """Tests for VivadoInstallation dataclass."""

    def test_to_dict(self) -> None:
        install = VivadoInstallation(
            version="2023.2",
            path=Path("/opt/Xilinx/Vivado/2023.2"),
            executable=Path("/opt/Xilinx/Vivado/2023.2/bin/vivado"),
        )
        result = install.to_dict()
        assert result["version"] == "2023.2"
        # Use Path to normalize expected values for cross-platform compatibility
        assert result["path"] == str(Path("/opt/Xilinx/Vivado/2023.2"))
        assert result["executable"] == str(Path("/opt/Xilinx/Vivado/2023.2/bin/vivado"))


class TestDetectVivadoInstallations:
    """Tests for Vivado installation detection."""

    def test_no_installations(self, tmp_path: Path) -> None:
        """Test detection when no Vivado is installed."""
        # Search in an empty directory
        result = detect_vivado_installations(search_paths=[tmp_path])
        assert result == []

    def test_detect_single_installation(self, tmp_path: Path) -> None:
        """Test detecting a single Vivado installation."""
        # Create mock Vivado installation
        vivado_base = tmp_path / "Xilinx" / "Vivado"
        vivado_2023 = vivado_base / "2023.2"
        vivado_bin = vivado_2023 / "bin"
        vivado_bin.mkdir(parents=True)

        # Create mock executable for current platform
        vivado_exec = create_mock_vivado_executable(vivado_bin)

        result = detect_vivado_installations(search_paths=[vivado_base])
        assert len(result) == 1
        assert result[0].version == "2023.2"
        assert result[0].path == vivado_2023
        assert result[0].executable == vivado_exec

    def test_detect_multiple_installations_sorted(self, tmp_path: Path) -> None:
        """Test that multiple installations are sorted by version (newest first)."""
        vivado_base = tmp_path / "Xilinx" / "Vivado"

        # Create multiple versions
        for version in ["2021.2", "2023.2", "2022.1"]:
            version_dir = vivado_base / version / "bin"
            version_dir.mkdir(parents=True)
            create_mock_vivado_executable(version_dir)

        result = detect_vivado_installations(search_paths=[vivado_base])
        assert len(result) == 3
        # Should be sorted newest first
        assert result[0].version == "2023.2"
        assert result[1].version == "2022.1"
        assert result[2].version == "2021.2"

    @pytest.mark.skipif(os.name != "nt", reason="Windows-specific test")
    def test_detect_windows_installation(self, tmp_path: Path) -> None:
        """Test detecting Windows-style Vivado installation."""
        vivado_base = tmp_path / "Xilinx" / "Vivado"
        vivado_2023 = vivado_base / "2023.2"
        vivado_bin = vivado_2023 / "bin"
        vivado_bin.mkdir(parents=True)

        # Create mock Windows executable
        (vivado_bin / "vivado.bat").touch()

        result = detect_vivado_installations(search_paths=[vivado_base])
        assert len(result) == 1
        assert result[0].executable.name == "vivado.bat"


class TestGetDefaultVivado:
    """Tests for get_default_vivado function."""

    def test_no_installations(self) -> None:
        """Test when no installations exist."""
        with patch(
            "vivado_mcp.vivado.detection.detect_vivado_installations",
            return_value=[],
        ):
            result = get_default_vivado()
            assert result is None

    def test_returns_newest_by_default(self, tmp_path: Path) -> None:
        """Test that newest version is returned by default."""
        vivado_base = tmp_path / "Xilinx" / "Vivado"

        # Create multiple versions
        for version in ["2021.2", "2023.2"]:
            version_dir = vivado_base / version / "bin"
            version_dir.mkdir(parents=True)
            create_mock_vivado_executable(version_dir)

        with patch(
            "vivado_mcp.vivado.detection._get_search_paths",
            return_value=[vivado_base],
        ):
            result = get_default_vivado()
            assert result is not None
            assert result.version == "2023.2"

    def test_override_version(self, tmp_path: Path) -> None:
        """Test selecting a specific version."""
        vivado_base = tmp_path / "Xilinx" / "Vivado"

        for version in ["2021.2", "2023.2"]:
            version_dir = vivado_base / version / "bin"
            version_dir.mkdir(parents=True)
            create_mock_vivado_executable(version_dir)

        with patch(
            "vivado_mcp.vivado.detection._get_search_paths",
            return_value=[vivado_base],
        ):
            result = get_default_vivado(override_version="2021.2")
            assert result is not None
            assert result.version == "2021.2"

    def test_override_path(self, tmp_path: Path) -> None:
        """Test using an explicit path override."""
        vivado_path = tmp_path / "custom" / "vivado" / "2023.2"
        vivado_bin = vivado_path / "bin"
        vivado_bin.mkdir(parents=True)
        create_mock_vivado_executable(vivado_bin)

        result = get_default_vivado(override_path=vivado_path)
        assert result is not None
        assert result.version == "2023.2"
        assert result.path == vivado_path

    def test_override_path_not_found(self, tmp_path: Path) -> None:
        """Test override path that doesn't exist."""
        result = get_default_vivado(override_path=tmp_path / "nonexistent")
        assert result is None

    def test_override_version_not_found(self, tmp_path: Path) -> None:
        """Test requesting a version that doesn't exist."""
        vivado_base = tmp_path / "Xilinx" / "Vivado"
        version_dir = vivado_base / "2023.2" / "bin"
        version_dir.mkdir(parents=True)
        create_mock_vivado_executable(version_dir)

        with patch(
            "vivado_mcp.vivado.detection._get_search_paths",
            return_value=[vivado_base],
        ):
            result = get_default_vivado(override_version="2024.1")
            assert result is None
