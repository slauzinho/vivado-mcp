"""Vivado build execution module.

This module provides functionality to run complete Vivado build flows
(synthesis -> implementation -> bitstream) in batch mode.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from vivado_mcp.vivado.detection import VivadoInstallation, get_default_vivado


@dataclass
class BuildMessage:
    """Represents an error or warning from the Vivado build."""

    severity: str  # ERROR, CRITICAL WARNING, WARNING
    id: str  # e.g., "Synth 8-87"
    message: str
    file: str | None = None
    line: int | None = None

    def to_dict(self) -> dict[str, str | int | None]:
        """Convert to dictionary for JSON serialization."""
        return {
            "severity": self.severity,
            "id": self.id,
            "message": self.message,
            "file": self.file,
            "line": self.line,
        }


@dataclass
class BuildResult:
    """Represents the result of a Vivado build."""

    success: bool
    project_path: str
    vivado_version: str
    errors: list[BuildMessage] = field(default_factory=list)
    critical_warnings: list[BuildMessage] = field(default_factory=list)
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""

    def to_dict(self) -> dict[str, object]:
        """Convert to dictionary for JSON serialization."""
        return {
            "success": self.success,
            "project_path": self.project_path,
            "vivado_version": self.vivado_version,
            "errors": [e.to_dict() for e in self.errors],
            "critical_warnings": [w.to_dict() for w in self.critical_warnings],
            "error_count": len(self.errors),
            "critical_warning_count": len(self.critical_warnings),
            "exit_code": self.exit_code,
        }


# Regex patterns for parsing Vivado output
# Vivado messages follow format: SEVERITY: [ID] message
_MESSAGE_PATTERN = re.compile(
    r"^(ERROR|CRITICAL WARNING|WARNING):\s*\[([^\]]+)\]\s*(.+)$",
    re.MULTILINE,
)

# Pattern to extract file:line from messages
_FILE_LINE_PATTERN = re.compile(r"['\"](.*?)['\"](?:\s+line\s+(\d+))?")


def parse_vivado_output(output: str) -> tuple[list[BuildMessage], list[BuildMessage]]:
    """Parse Vivado output for errors and critical warnings.

    Args:
        output: The stdout/stderr output from Vivado

    Returns:
        Tuple of (errors, critical_warnings) as lists of BuildMessage
    """
    errors: list[BuildMessage] = []
    critical_warnings: list[BuildMessage] = []

    for match in _MESSAGE_PATTERN.finditer(output):
        severity = match.group(1)
        msg_id = match.group(2)
        message = match.group(3).strip()

        # Try to extract file and line from the message
        file_match = _FILE_LINE_PATTERN.search(message)
        file_path: str | None = None
        line_num: int | None = None

        if file_match:
            file_path = file_match.group(1)
            if file_match.group(2):
                line_num = int(file_match.group(2))

        build_msg = BuildMessage(
            severity=severity,
            id=msg_id,
            message=message,
            file=file_path,
            line=line_num,
        )

        if severity == "ERROR":
            errors.append(build_msg)
        elif severity == "CRITICAL WARNING":
            critical_warnings.append(build_msg)

    return errors, critical_warnings


def _generate_build_tcl(project_path: Path, stop_on_error: bool = True) -> str:
    """Generate TCL script for running a full build.

    Args:
        project_path: Path to the project (.xpr or .tcl)
        stop_on_error: If True, stop the build on first error

    Returns:
        TCL script content as a string
    """
    project_path_tcl = str(project_path).replace("\\", "/")
    is_xpr = project_path.suffix.lower() == ".xpr"

    tcl_lines = [
        "# Auto-generated build script for vivado-mcp",
        "# Stop on first error if requested",
    ]

    if stop_on_error:
        tcl_lines.append("set_msg_config -severity ERROR -stop true")

    if is_xpr:
        # For .xpr files, open the project
        tcl_lines.extend([
            f'open_project "{project_path_tcl}"',
            "",
            "# Run synthesis",
            "reset_run synth_1",
            "launch_runs synth_1 -jobs 4",
            "wait_on_run synth_1",
            "",
            "# Check synthesis result",
            'if {[get_property PROGRESS [get_runs synth_1]] != "100%"} {',
            '    puts "ERROR: Synthesis failed"',
            "    exit 1",
            "}",
            'if {[get_property STATUS [get_runs synth_1]] != "synth_design Complete!"} {',
            '    puts "ERROR: Synthesis did not complete successfully"',
            "    exit 1",
            "}",
            "",
            "# Run implementation",
            "reset_run impl_1",
            "launch_runs impl_1 -jobs 4",
            "wait_on_run impl_1",
            "",
            "# Check implementation result",
            'if {[get_property PROGRESS [get_runs impl_1]] != "100%"} {',
            '    puts "ERROR: Implementation failed"',
            "    exit 1",
            "}",
            "",
            "# Generate bitstream",
            "launch_runs impl_1 -to_step write_bitstream -jobs 4",
            "wait_on_run impl_1",
            "",
            "# Final status",
            'puts "Build completed successfully"',
            "close_project",
            "exit 0",
        ])
    else:
        # For .tcl files, source them directly
        # The TCL file is expected to set up everything
        tcl_lines.extend([
            f'source "{project_path_tcl}"',
            "",
            "# Run synthesis",
            "synth_design",
            "",
            "# Run implementation",
            "opt_design",
            "place_design",
            "route_design",
            "",
            "# Generate bitstream",
            "write_bitstream -force [get_property DIRECTORY [current_project]]/output.bit",
            "",
            'puts "Build completed successfully"',
            "exit 0",
        ])

    return "\n".join(tcl_lines)


def _validate_project_path(project_path: str | Path) -> tuple[Path, str | None]:
    """Validate the project path.

    Args:
        project_path: Path to the project file

    Returns:
        Tuple of (validated_path, error_message)
        error_message is None if validation passed
    """
    path = Path(project_path)

    if not path.exists():
        return path, f"Project file not found: {path}"

    if not path.is_file():
        return path, f"Project path is not a file: {path}"

    suffix = path.suffix.lower()
    if suffix not in (".xpr", ".tcl"):
        return path, f"Invalid project file type '{suffix}'. Expected .xpr or .tcl"

    return path, None


async def run_vivado_build(
    project_path: str | Path,
    vivado_install: VivadoInstallation | None = None,
    timeout: int | None = None,
) -> BuildResult:
    """Run a complete Vivado build flow.

    This runs synthesis -> implementation -> bitstream generation in batch mode.
    The build stops immediately on the first error.

    Args:
        project_path: Path to the Vivado project (.xpr) or TCL script (.tcl)
        vivado_install: Optional specific Vivado installation to use.
                       If None, auto-detects the installation.
        timeout: Optional timeout in seconds for the build process.

    Returns:
        BuildResult containing success status, errors, and warnings
    """
    # Validate project path
    validated_path, error = _validate_project_path(project_path)
    if error:
        return BuildResult(
            success=False,
            project_path=str(project_path),
            vivado_version="unknown",
            errors=[BuildMessage(
                severity="ERROR",
                id="vivado-mcp",
                message=error,
            )],
            exit_code=-1,
        )

    # Get Vivado installation
    if vivado_install is None:
        vivado_install = get_default_vivado()

    if vivado_install is None:
        return BuildResult(
            success=False,
            project_path=str(validated_path),
            vivado_version="unknown",
            errors=[BuildMessage(
                severity="ERROR",
                id="vivado-mcp",
                message="No Vivado installation found. Install Vivado or set VIVADO_PATH.",
            )],
            exit_code=-1,
        )

    # Generate the build TCL script
    build_tcl = _generate_build_tcl(validated_path)

    # Write TCL script to a temporary file
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".tcl",
        delete=False,
        prefix="vivado_build_",
    ) as tcl_file:
        tcl_file.write(build_tcl)
        tcl_path = tcl_file.name

    try:
        # Build command for batch mode execution
        vivado_exe = str(vivado_install.executable)
        cmd = [
            vivado_exe,
            "-mode", "batch",
            "-source", tcl_path,
            "-nojournal",
            "-nolog",
        ]

        # Set working directory to project directory
        cwd = validated_path.parent

        # Create subprocess
        # On Windows, we would add creationflags to prevent console window,
        # but we keep it simple and portable here
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        try:
            # Wait for completion with optional timeout
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return BuildResult(
                success=False,
                project_path=str(validated_path),
                vivado_version=vivado_install.version,
                errors=[BuildMessage(
                    severity="ERROR",
                    id="vivado-mcp",
                    message=f"Build timed out after {timeout} seconds",
                )],
                exit_code=-1,
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        # Parse output for errors and warnings
        combined_output = stdout + "\n" + stderr
        errors, critical_warnings = parse_vivado_output(combined_output)

        # Determine success
        exit_code = process.returncode or 0
        success = exit_code == 0 and len(errors) == 0

        return BuildResult(
            success=success,
            project_path=str(validated_path),
            vivado_version=vivado_install.version,
            errors=errors,
            critical_warnings=critical_warnings,
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )

    finally:
        # Clean up temporary TCL file
        try:
            os.unlink(tcl_path)
        except OSError:
            pass
