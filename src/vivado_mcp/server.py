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
from vivado_mcp.vivado.build import (
    get_build_status,
    run_bitstream_generation,
    run_implementation,
    run_synthesis,
    run_vivado_build,
)
from vivado_mcp.vivado.clean import clean_build_outputs
from vivado_mcp.vivado.detection import (
    VivadoInstallation,
    detect_vivado_installations,
    get_default_vivado,
)
from vivado_mcp.vivado.session import (
    get_session_manager,
    run_tcl_command_with_fallback,
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
        Tool(
            name="run_build",
            description=(
                "Run a complete Vivado build flow (synthesis -> implementation -> bitstream). "
                "Executes Vivado in batch mode with no GUI. "
                "The build stops immediately on the first error. "
                "Returns success/failure status with any errors or critical warnings. "
                "Uses auto-detected Vivado installation unless a specific version is requested."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": (
                            "Path to the Vivado project file (.xpr) or TCL build script (.tcl). "
                            "For .xpr files, runs synth_1 and impl_1 design runs. "
                            "For .tcl files, sources the script and runs synth/impl/bitstream."
                        ),
                    },
                    "vivado_version": {
                        "type": "string",
                        "description": (
                            "Optional specific Vivado version to use (e.g., '2023.2'). "
                            "If not provided, uses the auto-detected default installation."
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "description": (
                            "Optional timeout in seconds for the build process. "
                            "If not provided, the build runs until completion or error."
                        ),
                    },
                },
                "required": ["project_path"],
            },
        ),
        Tool(
            name="run_synthesis",
            description=(
                "Run Vivado synthesis only (without implementation or bitstream). "
                "Allows quick checking for synthesis errors without running the full build flow. "
                "Executes Vivado in batch mode with no GUI. "
                "The synthesis stops immediately on the first error. "
                "Returns success/failure status with any errors or critical warnings. "
                "Uses auto-detected Vivado installation unless a specific version is requested."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": (
                            "Path to the Vivado project file (.xpr) or TCL build script (.tcl). "
                            "For .xpr files, runs the synth_1 design run. "
                            "For .tcl files, sources the script and runs synth_design."
                        ),
                    },
                    "vivado_version": {
                        "type": "string",
                        "description": (
                            "Optional specific Vivado version to use (e.g., '2023.2'). "
                            "If not provided, uses the auto-detected default installation."
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "description": (
                            "Optional timeout in seconds for the synthesis process. "
                            "If not provided, synthesis runs until completion or error."
                        ),
                    },
                },
                "required": ["project_path"],
            },
        ),
        Tool(
            name="run_implementation",
            description=(
                "Run Vivado implementation only (after synthesis is complete). "
                "Allows testing place and route without regenerating synthesis. "
                "Requires completed synthesis before running. "
                "Executes Vivado in batch mode with no GUI. "
                "The implementation stops immediately on the first error. "
                "Returns success/failure status with any errors or critical warnings. "
                "Uses auto-detected Vivado installation unless a specific version is requested."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": (
                            "Path to the Vivado project file (.xpr) or TCL build script (.tcl). "
                            "For .xpr files, requires synth_1 to be complete before running. "
                            "For .tcl files, sources the script and runs implementation steps."
                        ),
                    },
                    "vivado_version": {
                        "type": "string",
                        "description": (
                            "Optional specific Vivado version to use (e.g., '2023.2'). "
                            "If not provided, uses the auto-detected default installation."
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "description": (
                            "Optional timeout in seconds for the implementation process. "
                            "If not provided, implementation runs until completion or error."
                        ),
                    },
                },
                "required": ["project_path"],
            },
        ),
        Tool(
            name="generate_bitstream",
            description=(
                "Generate bitstream only (after implementation is complete). "
                "Allows regenerating the bitstream without re-running implementation. "
                "Requires completed implementation before running. "
                "Executes Vivado in batch mode with no GUI. "
                "Returns success/failure status with the bitstream file path. "
                "Uses auto-detected Vivado installation unless a specific version is requested."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": (
                            "Path to the Vivado project file (.xpr) or TCL build script (.tcl). "
                            "For .xpr files, requires impl_1 to be complete before running. "
                            "For .tcl files, sources the script and generates the bitstream."
                        ),
                    },
                    "vivado_version": {
                        "type": "string",
                        "description": (
                            "Optional specific Vivado version to use (e.g., '2023.2'). "
                            "If not provided, uses the auto-detected default installation."
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "description": (
                            "Optional timeout in seconds for the bitstream generation process. "
                            "If not provided, generation runs until completion or error."
                        ),
                    },
                },
                "required": ["project_path"],
            },
        ),
        Tool(
            name="clean_build",
            description=(
                "Clean Vivado build output directories to allow fresh rebuilds. "
                "Removes default Vivado output directories (.runs/, .cache/, .gen/, "
                ".hw/, .ip_user_files/) while preserving source files, constraints, "
                "and project configuration. "
                "Returns confirmation of cleaned directories."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": (
                            "Path to the Vivado project file (.xpr) or project directory. "
                            "The output directories in the same folder will be cleaned."
                        ),
                    },
                },
                "required": ["project_path"],
            },
        ),
        Tool(
            name="get_build_status",
            description=(
                "Check if a previous Vivado build completed successfully. "
                "Reads Vivado run status from the .runs directory to determine "
                "the current build state. Returns the overall state (not_started, "
                "in_progress, completed, failed) along with synthesis and implementation "
                "run details. Includes timestamp of last build attempt if available."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "project_path": {
                        "type": "string",
                        "description": (
                            "Path to the Vivado project file (.xpr) or project directory. "
                            "The .runs directory in the same folder will be checked for status."
                        ),
                    },
                },
                "required": ["project_path"],
            },
        ),
        Tool(
            name="start_tcl_session",
            description=(
                "Start a persistent Vivado TCL shell session. "
                "Subsequent TCL commands can reuse this session, avoiding Vivado "
                "startup overhead. The session remains active until explicitly closed. "
                "Returns session ID and status information."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "vivado_version": {
                        "type": "string",
                        "description": (
                            "Optional specific Vivado version to use (e.g., '2023.2'). "
                            "If not provided, uses the auto-detected default installation."
                        ),
                    },
                    "working_directory": {
                        "type": "string",
                        "description": (
                            "Optional working directory for the session. "
                            "Commands will execute relative to this directory."
                        ),
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="run_tcl_command",
            description=(
                "Execute a TCL command in a Vivado session. "
                "If a persistent session is available, uses it for faster execution. "
                "Otherwise falls back to batch mode. Supports any valid Vivado TCL command. "
                "Returns command output, any errors, and execution time."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "The TCL command to execute (e.g., 'open_project myproj.xpr', "
                            "'get_property STATUS [get_runs synth_1]', 'puts $::env(HOME)')."
                        ),
                    },
                    "session_id": {
                        "type": "string",
                        "description": (
                            "Optional session ID to use. If not provided, uses the default "
                            "session or falls back to batch mode if no session is available."
                        ),
                    },
                    "timeout": {
                        "type": "number",
                        "description": (
                            "Optional timeout in seconds for the command. "
                            "Default is 300 seconds (5 minutes)."
                        ),
                    },
                },
                "required": ["command"],
            },
        ),
        Tool(
            name="close_tcl_session",
            description=(
                "Close a persistent Vivado TCL shell session. "
                "Gracefully terminates the Vivado process. "
                "If no session ID is provided, closes the default session."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {
                        "type": "string",
                        "description": (
                            "Optional session ID to close. If not provided, "
                            "closes the default session."
                        ),
                    },
                },
                "required": [],
            },
        ),
        Tool(
            name="list_tcl_sessions",
            description=(
                "List all active Vivado TCL shell sessions. "
                "Returns information about each session including state, "
                "Vivado version, start time, and command count."
            ),
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> Sequence[TextContent]:
    """Handle tool calls."""
    if name == "detect_vivado":
        return await _handle_detect_vivado(arguments)
    if name == "run_build":
        return await _handle_run_build(arguments)
    if name == "run_synthesis":
        return await _handle_run_synthesis(arguments)
    if name == "run_implementation":
        return await _handle_run_implementation(arguments)
    if name == "generate_bitstream":
        return await _handle_generate_bitstream(arguments)
    if name == "clean_build":
        return await _handle_clean_build(arguments)
    if name == "get_build_status":
        return await _handle_get_build_status(arguments)
    if name == "start_tcl_session":
        return await _handle_start_tcl_session(arguments)
    if name == "run_tcl_command":
        return await _handle_run_tcl_command(arguments)
    if name == "close_tcl_session":
        return await _handle_close_tcl_session(arguments)
    if name == "list_tcl_sessions":
        return await _handle_list_tcl_sessions(arguments)

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


async def _handle_run_build(arguments: dict[str, Any]) -> Sequence[TextContent]:
    """Handle the run_build tool call.

    Args:
        arguments: Tool arguments containing 'project_path' and optional
                  'vivado_version' and 'timeout' fields

    Returns:
        List of TextContent with build results
    """
    import json

    project_path: str | None = arguments.get("project_path")
    vivado_version: str | None = arguments.get("vivado_version")
    timeout: int | None = arguments.get("timeout")

    # Validate required arguments
    if not project_path:
        result = {
            "success": False,
            "error": "Missing required argument: project_path",
        }
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    # Get Vivado installation
    vivado_install: VivadoInstallation | None = None
    if vivado_version:
        vivado_install = get_default_vivado(override_version=vivado_version)
        if vivado_install is None:
            result = {
                "success": False,
                "error": f"Requested Vivado version '{vivado_version}' not found",
            }
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
    else:
        # Use auto-detected installation
        config = get_config()
        if config.vivado_path:
            vivado_install = get_default_vivado(override_path=config.vivado_path)
        elif config.vivado_version:
            vivado_install = get_default_vivado(override_version=config.vivado_version)
        else:
            vivado_install = get_default_vivado()

    # Run the build
    build_result = await run_vivado_build(
        project_path=project_path,
        vivado_install=vivado_install,
        timeout=timeout,
    )

    return [TextContent(type="text", text=json.dumps(build_result.to_dict(), indent=2))]


async def _handle_run_synthesis(arguments: dict[str, Any]) -> Sequence[TextContent]:
    """Handle the run_synthesis tool call.

    Args:
        arguments: Tool arguments containing 'project_path' and optional
                  'vivado_version' and 'timeout' fields

    Returns:
        List of TextContent with synthesis results
    """
    import json

    project_path: str | None = arguments.get("project_path")
    vivado_version: str | None = arguments.get("vivado_version")
    timeout: int | None = arguments.get("timeout")

    # Validate required arguments
    if not project_path:
        result = {
            "success": False,
            "error": "Missing required argument: project_path",
        }
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    # Get Vivado installation
    vivado_install: VivadoInstallation | None = None
    if vivado_version:
        vivado_install = get_default_vivado(override_version=vivado_version)
        if vivado_install is None:
            result = {
                "success": False,
                "error": f"Requested Vivado version '{vivado_version}' not found",
            }
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
    else:
        # Use auto-detected installation
        config = get_config()
        if config.vivado_path:
            vivado_install = get_default_vivado(override_path=config.vivado_path)
        elif config.vivado_version:
            vivado_install = get_default_vivado(override_version=config.vivado_version)
        else:
            vivado_install = get_default_vivado()

    # Run synthesis only
    synth_result = await run_synthesis(
        project_path=project_path,
        vivado_install=vivado_install,
        timeout=timeout,
    )

    return [TextContent(type="text", text=json.dumps(synth_result.to_dict(), indent=2))]


async def _handle_run_implementation(arguments: dict[str, Any]) -> Sequence[TextContent]:
    """Handle the run_implementation tool call.

    Args:
        arguments: Tool arguments containing 'project_path' and optional
                  'vivado_version' and 'timeout' fields

    Returns:
        List of TextContent with implementation results
    """
    import json

    project_path: str | None = arguments.get("project_path")
    vivado_version: str | None = arguments.get("vivado_version")
    timeout: int | None = arguments.get("timeout")

    # Validate required arguments
    if not project_path:
        result = {
            "success": False,
            "error": "Missing required argument: project_path",
        }
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    # Get Vivado installation
    vivado_install: VivadoInstallation | None = None
    if vivado_version:
        vivado_install = get_default_vivado(override_version=vivado_version)
        if vivado_install is None:
            result = {
                "success": False,
                "error": f"Requested Vivado version '{vivado_version}' not found",
            }
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
    else:
        # Use auto-detected installation
        config = get_config()
        if config.vivado_path:
            vivado_install = get_default_vivado(override_path=config.vivado_path)
        elif config.vivado_version:
            vivado_install = get_default_vivado(override_version=config.vivado_version)
        else:
            vivado_install = get_default_vivado()

    # Run implementation only
    impl_result = await run_implementation(
        project_path=project_path,
        vivado_install=vivado_install,
        timeout=timeout,
    )

    return [TextContent(type="text", text=json.dumps(impl_result.to_dict(), indent=2))]


async def _handle_generate_bitstream(arguments: dict[str, Any]) -> Sequence[TextContent]:
    """Handle the generate_bitstream tool call.

    Args:
        arguments: Tool arguments containing 'project_path' and optional
                  'vivado_version' and 'timeout' fields

    Returns:
        List of TextContent with bitstream generation results
    """
    import json

    project_path: str | None = arguments.get("project_path")
    vivado_version: str | None = arguments.get("vivado_version")
    timeout: int | None = arguments.get("timeout")

    # Validate required arguments
    if not project_path:
        result = {
            "success": False,
            "error": "Missing required argument: project_path",
        }
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    # Get Vivado installation
    vivado_install: VivadoInstallation | None = None
    if vivado_version:
        vivado_install = get_default_vivado(override_version=vivado_version)
        if vivado_install is None:
            result = {
                "success": False,
                "error": f"Requested Vivado version '{vivado_version}' not found",
            }
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
    else:
        # Use auto-detected installation
        config = get_config()
        if config.vivado_path:
            vivado_install = get_default_vivado(override_path=config.vivado_path)
        elif config.vivado_version:
            vivado_install = get_default_vivado(override_version=config.vivado_version)
        else:
            vivado_install = get_default_vivado()

    # Generate bitstream only
    bitstream_result = await run_bitstream_generation(
        project_path=project_path,
        vivado_install=vivado_install,
        timeout=timeout,
    )

    return [TextContent(type="text", text=json.dumps(bitstream_result.to_dict(), indent=2))]


async def _handle_clean_build(arguments: dict[str, Any]) -> Sequence[TextContent]:
    """Handle the clean_build tool call.

    Args:
        arguments: Tool arguments containing 'project_path' field

    Returns:
        List of TextContent with clean results
    """
    import json

    project_path: str | None = arguments.get("project_path")

    # Validate required arguments
    if not project_path:
        result = {
            "success": False,
            "error": "Missing required argument: project_path",
        }
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    # Clean the build outputs
    clean_result = clean_build_outputs(project_path=project_path)

    return [TextContent(type="text", text=json.dumps(clean_result.to_dict(), indent=2))]


async def _handle_get_build_status(arguments: dict[str, Any]) -> Sequence[TextContent]:
    """Handle the get_build_status tool call.

    Args:
        arguments: Tool arguments containing 'project_path' field

    Returns:
        List of TextContent with build status information
    """
    import json

    project_path: str | None = arguments.get("project_path")

    # Validate required arguments
    if not project_path:
        result = {
            "error": "Missing required argument: project_path",
        }
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    # Get the build status
    status = get_build_status(project_path=project_path)

    return [TextContent(type="text", text=json.dumps(status.to_dict(), indent=2))]


async def _handle_start_tcl_session(arguments: dict[str, Any]) -> Sequence[TextContent]:
    """Handle the start_tcl_session tool call.

    Args:
        arguments: Tool arguments containing optional 'vivado_version' and
                  'working_directory' fields

    Returns:
        List of TextContent with session information
    """
    import json

    vivado_version: str | None = arguments.get("vivado_version")
    working_directory: str | None = arguments.get("working_directory")

    # Get Vivado installation if version specified
    vivado_install: VivadoInstallation | None = None
    if vivado_version:
        vivado_install = get_default_vivado(override_version=vivado_version)
        if vivado_install is None:
            result = {
                "success": False,
                "error": f"Requested Vivado version '{vivado_version}' not found",
            }
            return [TextContent(type="text", text=json.dumps(result, indent=2))]
    else:
        # Use auto-detected installation
        config = get_config()
        if config.vivado_path:
            vivado_install = get_default_vivado(override_path=config.vivado_path)
        elif config.vivado_version:
            vivado_install = get_default_vivado(override_version=config.vivado_version)

    # Create and start the session
    manager = get_session_manager()
    session, success, message = await manager.create_session(
        vivado_install=vivado_install,
        working_directory=working_directory,
    )

    if success:
        result = {
            "success": True,
            "message": message,
            "session": session.get_info().to_dict(),
        }
    else:
        result = {
            "success": False,
            "error": message,
        }

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_run_tcl_command(arguments: dict[str, Any]) -> Sequence[TextContent]:
    """Handle the run_tcl_command tool call.

    Args:
        arguments: Tool arguments containing 'command' and optional
                  'session_id' and 'timeout' fields

    Returns:
        List of TextContent with command execution results
    """
    import json

    command: str | None = arguments.get("command")
    session_id: str | None = arguments.get("session_id")
    timeout: float = arguments.get("timeout", 300.0)

    # Validate required arguments
    if not command:
        result = {
            "success": False,
            "error": "Missing required argument: command",
        }
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    # Get Vivado installation for batch fallback
    config = get_config()
    vivado_install: VivadoInstallation | None = None
    if config.vivado_path:
        vivado_install = get_default_vivado(override_path=config.vivado_path)
    elif config.vivado_version:
        vivado_install = get_default_vivado(override_version=config.vivado_version)

    # Execute the command (with fallback to batch mode)
    cmd_result = await run_tcl_command_with_fallback(
        command=command,
        session_id=session_id,
        vivado_install=vivado_install,
        timeout=timeout,
    )

    return [TextContent(type="text", text=json.dumps(cmd_result.to_dict(), indent=2))]


async def _handle_close_tcl_session(arguments: dict[str, Any]) -> Sequence[TextContent]:
    """Handle the close_tcl_session tool call.

    Args:
        arguments: Tool arguments containing optional 'session_id' field

    Returns:
        List of TextContent with close result
    """
    import json

    session_id: str | None = arguments.get("session_id")

    manager = get_session_manager()
    success, message = await manager.close_session(session_id)

    result = {
        "success": success,
        "message": message,
    }

    return [TextContent(type="text", text=json.dumps(result, indent=2))]


async def _handle_list_tcl_sessions(arguments: dict[str, Any]) -> Sequence[TextContent]:
    """Handle the list_tcl_sessions tool call.

    Args:
        arguments: Tool arguments (currently unused)

    Returns:
        List of TextContent with session list
    """
    import json

    _ = arguments  # Unused

    manager = get_session_manager()
    sessions = manager.list_sessions()

    result = {
        "sessions": [s.to_dict() for s in sessions],
        "count": len(sessions),
        "default_session_id": manager.default_session_id,
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
