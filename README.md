# Vivado MCP Server

MCP (Model Context Protocol) server for Xilinx Vivado FPGA development automation. This server allows Claude Code and other MCP-compatible AI tools to interact with Vivado for building, synthesizing, and managing FPGA projects.

## Features

- **Automatic Vivado Detection** - Finds Vivado installations on Windows and Linux
- **Full Build Flow** - Run synthesis, implementation, and bitstream generation
- **Individual Build Steps** - Run synthesis, implementation, or bitstream separately
- **Persistent TCL Sessions** - Keep Vivado running for fast iterative commands
- **Build Status Checking** - Query the state of previous builds
- **Clean Builds** - Remove build artifacts for fresh rebuilds

## Prerequisites

- Python 3.10 or higher
- Xilinx Vivado installed (tested with 2023.x and 2024.x)
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Installation

### Option 1: Install from source with uv (Recommended for development)

```bash
# Clone the repository
git clone https://github.com/yourusername/vivado-mcp.git
cd vivado-mcp

# Install with uv
uv sync
```

### Option 2: Install with pip

```bash
pip install vivado-mcp
```

### Option 3: Install from source with pip

```bash
git clone https://github.com/yourusername/vivado-mcp.git
cd vivado-mcp
pip install -e .
```

## Using with Claude Code

Claude Code can use MCP servers to extend its capabilities. To add this server to Claude Code:

### Step 1: Add the MCP server configuration

Run this command to add the server to your Claude Code configuration:

```bash
claude mcp add vivado-mcp
```

When prompted, enter the command to start the server:

**If installed with uv (from source):**
```
uv run --directory /path/to/vivado-mcp vivado-mcp
```

**If installed with pip:**
```
vivado-mcp
```

### Step 2: Alternative - Manual configuration

You can also manually edit your Claude Code MCP settings. The settings file is located at:

- **macOS**: `~/.claude/claude_desktop_config.json`
- **Linux**: `~/.config/claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

Add the following to the `mcpServers` section:

**If installed with uv:**
```json
{
  "mcpServers": {
    "vivado-mcp": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/vivado-mcp", "vivado-mcp"]
    }
  }
}
```

**If installed with pip:**
```json
{
  "mcpServers": {
    "vivado-mcp": {
      "command": "vivado-mcp"
    }
  }
}
```

**With environment variables for Vivado configuration:**
```json
{
  "mcpServers": {
    "vivado-mcp": {
      "command": "vivado-mcp",
      "env": {
        "VIVADO_PATH": "/opt/Xilinx/Vivado/2023.2",
        "VIVADO_VERSION": "2023.2"
      }
    }
  }
}
```

### Step 3: Restart Claude Code

After adding the configuration, restart Claude Code for the changes to take effect. You can verify the server is running by asking Claude to list available tools or detect Vivado installations.

## Running the Server Manually

You can also run the server directly for testing:

```bash
# With uv
uv run vivado-mcp

# With pip install
vivado-mcp

# Or directly with Python
python -m vivado_mcp.server
```

## Configuration

The server can be configured via environment variables or a configuration file.

### Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `VIVADO_PATH` | Explicit path to a Vivado installation | `/opt/Xilinx/Vivado/2023.2` |
| `VIVADO_VERSION` | Specific version to use | `2023.2` |
| `VIVADO_SEARCH_PATHS` | Additional search paths (colon/semicolon separated) | `/custom/path1:/custom/path2` |

### Configuration File

Create a `vivado-mcp.json` file in your project directory or home directory:

```json
{
  "vivado_path": "/path/to/vivado/2023.2",
  "vivado_version": "2023.2",
  "additional_search_paths": ["/custom/search/path"]
}
```

### Vivado Auto-Detection

The server automatically searches for Vivado in standard locations:

- **Windows**: `C:\Xilinx\Vivado\*`
- **Linux**: `/opt/Xilinx/Vivado/*`, `/tools/Xilinx/Vivado/*`, `~/Xilinx/Vivado/*`
- **macOS**: `/Applications/Xilinx/Vivado/*`, `~/Xilinx/Vivado/*`

## Available Tools

### detect_vivado

Detects Vivado installations on the system.

**Parameters:**
- `version` (optional): Specific version to look for (e.g., "2023.2")
- `include_all` (optional): Return all installations instead of just the default

**Example usage in Claude:**
> "Detect what Vivado versions are installed on my system"

### run_build

Run a complete Vivado build flow (synthesis -> implementation -> bitstream).

**Parameters:**
- `project_path` (required): Path to the .xpr project file or .tcl build script
- `vivado_version` (optional): Specific Vivado version to use
- `timeout` (optional): Timeout in seconds

**Example usage in Claude:**
> "Build my Vivado project at /home/user/projects/fpga/myproject.xpr"

### run_synthesis

Run Vivado synthesis only (without implementation or bitstream).

**Parameters:**
- `project_path` (required): Path to the .xpr project file or .tcl build script
- `vivado_version` (optional): Specific Vivado version to use
- `timeout` (optional): Timeout in seconds

**Example usage in Claude:**
> "Run synthesis on my project to check for errors"

### run_implementation

Run Vivado implementation only (requires completed synthesis).

**Parameters:**
- `project_path` (required): Path to the .xpr project file or .tcl build script
- `vivado_version` (optional): Specific Vivado version to use
- `timeout` (optional): Timeout in seconds

### generate_bitstream

Generate bitstream only (requires completed implementation).

**Parameters:**
- `project_path` (required): Path to the .xpr project file or .tcl build script
- `vivado_version` (optional): Specific Vivado version to use
- `timeout` (optional): Timeout in seconds

### get_build_status

Check if a previous Vivado build completed successfully.

**Parameters:**
- `project_path` (required): Path to the .xpr project file or project directory

**Example usage in Claude:**
> "What's the status of my last build?"

### clean_build

Clean Vivado build output directories (.runs/, .cache/, .gen/, .hw/, .ip_user_files/).

**Parameters:**
- `project_path` (required): Path to the .xpr project file or project directory

**Example usage in Claude:**
> "Clean the build outputs so I can do a fresh build"

### start_tcl_session

Start a persistent Vivado TCL shell session for faster iterative commands.

**Parameters:**
- `vivado_version` (optional): Specific Vivado version to use
- `working_directory` (optional): Working directory for the session

### run_tcl_command

Execute a TCL command in a Vivado session (uses persistent session if available, otherwise batch mode).

**Parameters:**
- `command` (required): The TCL command to execute
- `session_id` (optional): Specific session ID to use
- `timeout` (optional): Timeout in seconds (default: 300)

**Example usage in Claude:**
> "Run the TCL command: get_property STATUS [get_runs synth_1]"

### close_tcl_session

Close a persistent Vivado TCL shell session.

**Parameters:**
- `session_id` (optional): Session ID to close (closes default if not specified)

### list_tcl_sessions

List all active Vivado TCL shell sessions.

## Troubleshooting

### Server not starting

1. Check that Python 3.10+ is installed: `python --version`
2. Verify the package is installed: `pip show vivado-mcp`
3. Try running the server manually to see errors: `vivado-mcp`

### Vivado not detected

1. Ensure Vivado is installed in a standard location
2. Set the `VIVADO_PATH` environment variable to your Vivado installation
3. Use the `detect_vivado` tool with `include_all: true` to see what was found

### Claude Code not seeing the server

1. Ensure the configuration file is in the correct location
2. Restart Claude Code after making configuration changes
3. Check that the command path is correct (use absolute paths if needed)

## Development

### Running tests

```bash
uv run pytest
```

### Type checking

```bash
uv run mypy src/
```

### Linting

```bash
uv run ruff check src/
```

## License

MIT
