"""Vivado MCP Server - Main server implementation.

This module provides the MCP server that exposes Vivado automation tools
to MCP clients like Claude.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from vivado_mcp.config import VivadoConfig
from vivado_mcp.vivado.detection import (
    VivadoInstallation,
    detect_vivado_installations,
    get_default_vivado,
)

# Create the MCP server instance
server = Server("vivado-mcp")

# Global configuration - loaded on startup
_config: VivadoConfig | None = None


def get_config() -> VivadoConfig:
    """Get the current configuration, loading it if necessary."""
    global _config
    if _config is None:
        _config = VivadoConfig.load()
    return _config


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List available Vivado MCP tools."""
    return [
        Tool(
            name="detect_vivado",
            description=(
                "Detect Vivado installations on the system. "
                "Returns information about all detected Vivado versions, "
                "including paths and the default version to use. "
                "Supports automatic detection on Windows (C:\\Xilinx\\Vivado\\*) "
                "and Linux (/opt/Xilinx/Vivado/*, ~/Xilinx/Vivado/*). "
                "Can optionally filter for a specific version."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "version": {
                        "type": "string",
                        "description": (
                            "Optional specific version to look for (e.g., '2023.2'). "
                            "If not provided, returns all detected versions."
                        ),
                    },
                    "include_all": {
                        "type": "boolean",
                        "description": (
                            "If true, returns all detected installations. "
                            "If false (default), returns only the default/selected installation."
                        ),
                        "default": False,
                    },
                },
                "required": [],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> Sequence[TextContent]:
    """Handle tool calls."""
    if name == "detect_vivado":
        return await _handle_detect_vivado(arguments)

    return [TextContent(type="text", text=f"Unknown tool: {name}")]


async def _handle_detect_vivado(arguments: dict[str, Any]) -> Sequence[TextContent]:
    """Handle the detect_vivado tool call.

    Args:
        arguments: Tool arguments containing optional 'version' and 'include_all' fields

    Returns:
        List of TextContent with detection results
    """
    config = get_config()
    requested_version: str | None = arguments.get("version")
    include_all: bool = arguments.get("include_all", False)

    # Build search paths including any additional configured paths
    additional_paths = config.additional_search_paths if config.additional_search_paths else None

    # Detect installations
    installations = detect_vivado_installations(search_paths=additional_paths)

    # Also search default paths and merge
    default_installations = detect_vivado_installations()
    seen_paths = {str(i.path) for i in installations}
    for inst in default_installations:
        if str(inst.path) not in seen_paths:
            installations.append(inst)
            seen_paths.add(str(inst.path))

    # Re-sort after merging
    installations.sort(
        key=lambda x: tuple(int(p) for p in x.version.split(".") if p.isdigit()),
        reverse=True,
    )

    # Determine the default installation
    default_install: VivadoInstallation | None = None

    if config.vivado_path:
        # Use configured path override
        default_install = get_default_vivado(override_path=config.vivado_path)
    elif config.vivado_version:
        # Use configured version override
        default_install = get_default_vivado(override_version=config.vivado_version)
    elif requested_version:
        # Use requested version
        default_install = get_default_vivado(override_version=requested_version)
    elif installations:
        # Use most recent version
        default_install = installations[0]

    # Build response
    if include_all:
        # Return all installations
        result: dict[str, Any] = {
            "installations": [inst.to_dict() for inst in installations],
            "count": len(installations),
            "default": default_install.to_dict() if default_install else None,
        }

        if not installations:
            result["message"] = (
                "No Vivado installations detected. "
                "Please ensure Vivado is installed in a standard location "
                "(Windows: C:\\Xilinx\\Vivado\\*, Linux: /opt/Xilinx/Vivado/*) "
                "or configure a custom path via VIVADO_PATH environment variable."
            )

        import json

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    else:
        # Return only the selected/default installation
        if default_install:
            import json

            result = {
                "found": True,
                "installation": default_install.to_dict(),
                "total_installations": len(installations),
            }
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
        else:
            import json

            result = {
                "found": False,
                "message": (
                    "No Vivado installation found. "
                    "Please ensure Vivado is installed in a standard location "
                    "(Windows: C:\\Xilinx\\Vivado\\*, Linux: /opt/Xilinx/Vivado/*) "
                    "or configure a custom path via VIVADO_PATH environment variable."
                ),
                "searched_version": requested_version,
            }
            return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def run_server() -> None:
    """Run the MCP server."""
    # Load configuration on startup
    global _config
    _config = VivadoConfig.load()

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    """Main entry point for the Vivado MCP server."""
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
