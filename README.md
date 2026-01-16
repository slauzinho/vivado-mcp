# Vivado MCP Server

MCP server for Xilinx Vivado FPGA development automation.

## Installation

```bash
pip install vivado-mcp
```

## Usage

The server can be run directly:

```bash
vivado-mcp
```

## Configuration

Configuration can be provided via environment variables or a configuration file.

### Environment Variables

- `VIVADO_PATH`: Explicit path to a Vivado installation directory
- `VIVADO_VERSION`: Specific version to use (e.g., "2023.2")
- `VIVADO_SEARCH_PATHS`: Additional search paths (colon or semicolon separated)

### Configuration File

Create a `vivado-mcp.json` file in your project directory or home directory:

```json
{
  "vivado_path": "/path/to/vivado/2023.2",
  "vivado_version": "2023.2",
  "additional_search_paths": ["/custom/search/path"]
}
```

## Tools

### detect_vivado

Detects Vivado installations on the system.

**Parameters:**
- `version` (optional): Specific version to look for
- `include_all` (optional): Return all installations instead of just the default

## License

MIT
