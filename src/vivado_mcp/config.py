"""Configuration management for Vivado MCP Server.

This module handles configuration loading and management, supporting
both environment variables and configuration files for overriding
default Vivado detection behavior.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class VivadoConfig:
    """Configuration for Vivado MCP Server.

    Attributes:
        vivado_path: Optional explicit path to a Vivado installation directory.
                     Overrides automatic detection.
        vivado_version: Optional specific version to use (e.g., "2023.2").
                        Only used if vivado_path is not set.
        additional_search_paths: Additional paths to search for Vivado installations.
    """

    vivado_path: Path | None = None
    vivado_version: str | None = None
    additional_search_paths: list[Path] = field(default_factory=list)

    @classmethod
    def from_env(cls) -> VivadoConfig:
        """Load configuration from environment variables.

        Environment variables:
            VIVADO_PATH: Explicit path to Vivado installation
            VIVADO_VERSION: Specific version to use
            VIVADO_SEARCH_PATHS: Additional search paths (colon or semicolon separated)

        Returns:
            VivadoConfig instance with values from environment
        """
        vivado_path: Path | None = None
        vivado_version: str | None = None
        additional_paths: list[Path] = []

        # VIVADO_PATH - explicit installation path
        env_path = os.environ.get("VIVADO_PATH")
        if env_path:
            vivado_path = Path(env_path)

        # VIVADO_VERSION - specific version
        env_version = os.environ.get("VIVADO_VERSION")
        if env_version:
            vivado_version = env_version

        # VIVADO_SEARCH_PATHS - additional search paths
        env_search = os.environ.get("VIVADO_SEARCH_PATHS")
        if env_search:
            # Support both : and ; as separators for cross-platform compatibility
            separator = ";" if ";" in env_search else ":"
            for path_str in env_search.split(separator):
                path_str = path_str.strip()
                if path_str:
                    additional_paths.append(Path(path_str))

        return cls(
            vivado_path=vivado_path,
            vivado_version=vivado_version,
            additional_search_paths=additional_paths,
        )

    @classmethod
    def from_file(cls, config_path: Path) -> VivadoConfig:
        """Load configuration from a JSON file.

        Args:
            config_path: Path to the configuration file

        Returns:
            VivadoConfig instance with values from the file

        Raises:
            FileNotFoundError: If the config file doesn't exist
            json.JSONDecodeError: If the file contains invalid JSON
        """
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)

        vivado_path: Path | None = None
        if "vivado_path" in data and data["vivado_path"]:
            vivado_path = Path(data["vivado_path"])

        additional_paths: list[Path] = []
        if "additional_search_paths" in data:
            for path_str in data["additional_search_paths"]:
                additional_paths.append(Path(path_str))

        return cls(
            vivado_path=vivado_path,
            vivado_version=data.get("vivado_version"),
            additional_search_paths=additional_paths,
        )

    @classmethod
    def load(cls, config_path: Path | None = None) -> VivadoConfig:
        """Load configuration with fallback hierarchy.

        Priority (highest to lowest):
        1. Environment variables
        2. Specified config file
        3. Default config file locations
        4. Default values

        Args:
            config_path: Optional explicit path to config file

        Returns:
            Merged VivadoConfig instance
        """
        # Start with defaults
        config = cls()

        # Try to load from config file
        file_config: VivadoConfig | None = None

        if config_path is not None and config_path.exists():
            file_config = cls.from_file(config_path)
        else:
            # Try default config locations
            default_locations = [
                Path.cwd() / "vivado-mcp.json",
                Path.cwd() / ".vivado-mcp.json",
                Path.home() / ".config" / "vivado-mcp" / "config.json",
                Path.home() / ".vivado-mcp.json",
            ]
            for loc in default_locations:
                if loc.exists():
                    try:
                        file_config = cls.from_file(loc)
                        break
                    except (json.JSONDecodeError, PermissionError):
                        continue

        # Merge file config if found
        if file_config is not None:
            if file_config.vivado_path is not None:
                config.vivado_path = file_config.vivado_path
            if file_config.vivado_version is not None:
                config.vivado_version = file_config.vivado_version
            config.additional_search_paths.extend(file_config.additional_search_paths)

        # Override with environment variables (highest priority)
        env_config = cls.from_env()
        if env_config.vivado_path is not None:
            config.vivado_path = env_config.vivado_path
        if env_config.vivado_version is not None:
            config.vivado_version = env_config.vivado_version
        config.additional_search_paths.extend(env_config.additional_search_paths)

        return config

    def to_dict(self) -> dict[str, object]:
        """Convert configuration to dictionary for serialization."""
        return {
            "vivado_path": str(self.vivado_path) if self.vivado_path else None,
            "vivado_version": self.vivado_version,
            "additional_search_paths": [str(p) for p in self.additional_search_paths],
        }
