"""Tests for configuration module."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from vivado_mcp.config import VivadoConfig


class TestVivadoConfigFromEnv:
    """Tests for loading configuration from environment variables."""

    def test_empty_env(self) -> None:
        """Test loading with no environment variables set."""
        with patch.dict(os.environ, {}, clear=True):
            config = VivadoConfig.from_env()
            assert config.vivado_path is None
            assert config.vivado_version is None
            assert config.additional_search_paths == []

    def test_vivado_path_env(self) -> None:
        """Test VIVADO_PATH environment variable."""
        with patch.dict(os.environ, {"VIVADO_PATH": "/opt/Xilinx/Vivado/2023.2"}):
            config = VivadoConfig.from_env()
            assert config.vivado_path == Path("/opt/Xilinx/Vivado/2023.2")

    def test_vivado_version_env(self) -> None:
        """Test VIVADO_VERSION environment variable."""
        with patch.dict(os.environ, {"VIVADO_VERSION": "2023.2"}):
            config = VivadoConfig.from_env()
            assert config.vivado_version == "2023.2"

    def test_search_paths_colon_separated(self) -> None:
        """Test VIVADO_SEARCH_PATHS with colon separator (Unix-style)."""
        with patch.dict(os.environ, {"VIVADO_SEARCH_PATHS": "/path1:/path2:/path3"}):
            config = VivadoConfig.from_env()
            assert len(config.additional_search_paths) == 3
            assert config.additional_search_paths[0] == Path("/path1")
            assert config.additional_search_paths[1] == Path("/path2")
            assert config.additional_search_paths[2] == Path("/path3")

    def test_search_paths_semicolon_separated(self) -> None:
        """Test VIVADO_SEARCH_PATHS with semicolon separator (Windows-style)."""
        with patch.dict(
            os.environ, {"VIVADO_SEARCH_PATHS": "C:\\path1;D:\\path2;E:\\path3"}
        ):
            config = VivadoConfig.from_env()
            assert len(config.additional_search_paths) == 3


class TestVivadoConfigFromFile:
    """Tests for loading configuration from files."""

    def test_load_from_file(self, tmp_path: Path) -> None:
        """Test loading configuration from a JSON file."""
        config_file = tmp_path / "config.json"
        config_data = {
            "vivado_path": "/custom/vivado/path",
            "vivado_version": "2023.2",
            "additional_search_paths": ["/search/path1", "/search/path2"],
        }
        config_file.write_text(json.dumps(config_data))

        config = VivadoConfig.from_file(config_file)
        assert config.vivado_path == Path("/custom/vivado/path")
        assert config.vivado_version == "2023.2"
        assert len(config.additional_search_paths) == 2

    def test_load_partial_config(self, tmp_path: Path) -> None:
        """Test loading a config file with only some fields."""
        config_file = tmp_path / "config.json"
        config_data = {"vivado_version": "2024.1"}
        config_file.write_text(json.dumps(config_data))

        config = VivadoConfig.from_file(config_file)
        assert config.vivado_path is None
        assert config.vivado_version == "2024.1"
        assert config.additional_search_paths == []

    def test_file_not_found(self, tmp_path: Path) -> None:
        """Test error when config file doesn't exist."""
        with pytest.raises(FileNotFoundError):
            VivadoConfig.from_file(tmp_path / "nonexistent.json")


class TestVivadoConfigLoad:
    """Tests for the merged configuration loading."""

    def test_env_overrides_file(self, tmp_path: Path) -> None:
        """Test that environment variables override file config."""
        config_file = tmp_path / "vivado-mcp.json"
        config_data = {
            "vivado_path": "/file/path",
            "vivado_version": "2023.1",
        }
        config_file.write_text(json.dumps(config_data))

        with patch.dict(
            os.environ,
            {"VIVADO_PATH": "/env/path", "VIVADO_VERSION": "2023.2"},
        ):
            config = VivadoConfig.load(config_path=config_file)
            # Environment should override file
            assert config.vivado_path == Path("/env/path")
            assert config.vivado_version == "2023.2"

    def test_load_defaults_only(self, tmp_path: Path) -> None:
        """Test loading with no config file and no env vars."""
        # Change to tmp_path to avoid picking up any real config files in cwd
        with patch.dict(os.environ, {}, clear=True), patch(
            "vivado_mcp.config.Path.cwd", return_value=tmp_path
        ), patch("vivado_mcp.config.Path.home", return_value=tmp_path):
            # Use a non-existent path to avoid picking up any real config
            config = VivadoConfig.load(config_path=Path("/nonexistent/config.json"))
            assert config.vivado_path is None
            assert config.vivado_version is None


class TestVivadoConfigToDict:
    """Tests for configuration serialization."""

    def test_to_dict(self) -> None:
        """Test converting config to dictionary."""
        config = VivadoConfig(
            vivado_path=Path("/opt/vivado"),
            vivado_version="2023.2",
            additional_search_paths=[Path("/path1"), Path("/path2")],
        )
        result = config.to_dict()
        # Use Path to normalize expected values for cross-platform compatibility
        assert result["vivado_path"] == str(Path("/opt/vivado"))
        assert result["vivado_version"] == "2023.2"
        assert result["additional_search_paths"] == [str(Path("/path1")), str(Path("/path2"))]

    def test_to_dict_with_none(self) -> None:
        """Test converting config with None values."""
        config = VivadoConfig()
        result = config.to_dict()
        assert result["vivado_path"] is None
        assert result["vivado_version"] is None
        assert result["additional_search_paths"] == []
