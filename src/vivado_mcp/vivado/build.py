"""Vivado build execution module.

This module provides functionality to run complete Vivado build flows
(synthesis -> implementation -> bitstream) in batch mode, and to check
the status of previous builds.
"""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

from vivado_mcp.vivado.detection import VivadoInstallation, get_default_vivado


class BuildState(str, Enum):
    """Represents the current state of a Vivado build."""

    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class RunStatus:
    """Represents the status of a single Vivado run (synth_1, impl_1, etc.)."""

    name: str
    state: BuildState
    progress: str | None = None  # e.g., "100%"
    status_message: str | None = None  # e.g., "synth_design Complete!"
    timestamp: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "state": self.state.value,
            "progress": self.progress,
            "status_message": self.status_message,
            "timestamp": self.timestamp,
        }


@dataclass
class BuildStatus:
    """Represents the overall build status of a Vivado project."""

    project_path: str
    overall_state: BuildState
    synthesis: RunStatus | None = None
    implementation: RunStatus | None = None
    last_build_timestamp: str | None = None
    runs_directory_exists: bool = False

    def to_dict(self) -> dict[str, object]:
        """Convert to dictionary for JSON serialization."""
        return {
            "project_path": self.project_path,
            "overall_state": self.overall_state.value,
            "synthesis": self.synthesis.to_dict() if self.synthesis else None,
            "implementation": self.implementation.to_dict() if self.implementation else None,
            "last_build_timestamp": self.last_build_timestamp,
            "runs_directory_exists": self.runs_directory_exists,
        }


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


@dataclass
class BitstreamResult:
    """Represents the result of a Vivado bitstream generation."""

    success: bool
    project_path: str
    vivado_version: str
    bitstream_path: str | None = None
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
            "bitstream_path": self.bitstream_path,
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


def _generate_synthesis_tcl(project_path: Path) -> str:
    """Generate TCL script for running synthesis only.

    Args:
        project_path: Path to the project (.xpr or .tcl)

    Returns:
        TCL script content as a string
    """
    project_path_tcl = str(project_path).replace("\\", "/")
    is_xpr = project_path.suffix.lower() == ".xpr"

    tcl_lines = [
        "# Auto-generated synthesis script for vivado-mcp",
    ]

    if is_xpr:
        # For .xpr files, open the project and run synthesis only
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
            'puts "Synthesis completed successfully"',
            "close_project",
            "exit 0",
        ])
    else:
        # For .tcl files, source them and run synthesis only
        tcl_lines.extend([
            f'source "{project_path_tcl}"',
            "",
            "# Run synthesis only",
            "synth_design",
            "",
            'puts "Synthesis completed successfully"',
            "exit 0",
        ])

    return "\n".join(tcl_lines)


def _generate_implementation_tcl(project_path: Path) -> str:
    """Generate TCL script for running implementation only (after synthesis).

    Args:
        project_path: Path to the project (.xpr or .tcl)

    Returns:
        TCL script content as a string
    """
    project_path_tcl = str(project_path).replace("\\", "/")
    is_xpr = project_path.suffix.lower() == ".xpr"

    tcl_lines = [
        "# Auto-generated implementation script for vivado-mcp",
    ]

    if is_xpr:
        # For .xpr files, open the project and run implementation only
        tcl_lines.extend([
            f'open_project "{project_path_tcl}"',
            "",
            "# Verify synthesis is complete",
            'if {[get_property PROGRESS [get_runs synth_1]] != "100%"} {',
            '    puts "ERROR: Synthesis not complete. Run synthesis first."',
            "    exit 1",
            "}",
            'if {[get_property STATUS [get_runs synth_1]] != "synth_design Complete!"} {',
            '    puts "ERROR: Synthesis did not complete successfully. Run synthesis first."',
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
            'puts "Implementation completed successfully"',
            "close_project",
            "exit 0",
        ])
    else:
        # For .tcl files, source them and run implementation only
        # Assumes synth_design has already been run and design is in memory
        tcl_lines.extend([
            f'source "{project_path_tcl}"',
            "",
            "# Run implementation only (assumes synthesis checkpoint exists)",
            "opt_design",
            "place_design",
            "route_design",
            "",
            "# Generate bitstream",
            "write_bitstream -force [get_property DIRECTORY [current_project]]/output.bit",
            "",
            'puts "Implementation completed successfully"',
            "exit 0",
        ])

    return "\n".join(tcl_lines)


def _generate_bitstream_tcl(project_path: Path) -> str:
    """Generate TCL script for generating bitstream only (after implementation).

    Args:
        project_path: Path to the project (.xpr or .tcl)

    Returns:
        TCL script content as a string
    """
    project_path_tcl = str(project_path).replace("\\", "/")
    is_xpr = project_path.suffix.lower() == ".xpr"

    tcl_lines = [
        "# Auto-generated bitstream script for vivado-mcp",
    ]

    if is_xpr:
        # For .xpr files, open the project and generate bitstream only
        tcl_lines.extend([
            f'open_project "{project_path_tcl}"',
            "",
            "# Verify synthesis is complete",
            'if {[get_property PROGRESS [get_runs synth_1]] != "100%"} {',
            '    puts "ERROR: Synthesis not complete. Run synthesis first."',
            "    exit 1",
            "}",
            "",
            "# Verify implementation is complete",
            'if {[get_property PROGRESS [get_runs impl_1]] != "100%"} {',
            '    puts "ERROR: Implementation not complete. Run implementation first."',
            "    exit 1",
            "}",
            "",
            "# Generate bitstream only",
            "launch_runs impl_1 -to_step write_bitstream -jobs 4",
            "wait_on_run impl_1",
            "",
            "# Find and report bitstream file path",
            "set impl_dir [get_property DIRECTORY [get_runs impl_1]]",
            'set bit_files [glob -nocomplain -directory $impl_dir "*.bit"]',
            'if {[llength $bit_files] > 0} {',
            '    puts "BITSTREAM_FILE: [lindex $bit_files 0]"',
            '} else {',
            '    puts "ERROR: Bitstream file not found after generation"',
            "    exit 1",
            "}",
            "",
            'puts "Bitstream generation completed successfully"',
            "close_project",
            "exit 0",
        ])
    else:
        # For .tcl files, source them and generate bitstream only
        # Assumes implementation has already been run
        tcl_lines.extend([
            f'source "{project_path_tcl}"',
            "",
            "# Generate bitstream (assumes design is routed)",
            "set output_dir [get_property DIRECTORY [current_project]]",
            "set bitstream_path ${output_dir}/output.bit",
            "write_bitstream -force $bitstream_path",
            "",
            'puts "BITSTREAM_FILE: $bitstream_path"',
            'puts "Bitstream generation completed successfully"',
            "exit 0",
        ])

    return "\n".join(tcl_lines)


def _generate_build_tcl(project_path: Path) -> str:
    """Generate TCL script for running a full build.

    Args:
        project_path: Path to the project (.xpr or .tcl)

    Returns:
        TCL script content as a string
    """
    project_path_tcl = str(project_path).replace("\\", "/")
    is_xpr = project_path.suffix.lower() == ".xpr"

    tcl_lines = [
        "# Auto-generated build script for vivado-mcp",
    ]

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


def _get_run_directory_timestamp(run_dir: Path) -> str | None:
    """Get the most recent modification timestamp from a run directory.

    Args:
        run_dir: Path to the run directory (e.g., synth_1, impl_1)

    Returns:
        ISO format timestamp string or None if cannot be determined
    """
    try:
        # Look for common status files in the run directory
        status_files = [
            run_dir / "runme.log",
            run_dir / "vivado.pb",
            run_dir / ".vivado.begin.rst",
            run_dir / ".vivado.end.rst",
        ]

        most_recent: float | None = None
        for status_file in status_files:
            if status_file.exists():
                mtime = status_file.stat().st_mtime
                if most_recent is None or mtime > most_recent:
                    most_recent = mtime

        # If no status files found, use the directory mtime
        if most_recent is None and run_dir.exists():
            most_recent = run_dir.stat().st_mtime

        if most_recent is not None:
            return datetime.fromtimestamp(most_recent).isoformat()

    except OSError:
        pass

    return None


def _parse_run_status(run_dir: Path, run_name: str) -> RunStatus:
    """Parse the status of a Vivado run from its directory.

    Vivado stores run status in several ways:
    - .vivado.begin.rst / .vivado.end.rst markers
    - runme.log for detailed progress
    - vivado.pb for progress information

    Args:
        run_dir: Path to the run directory
        run_name: Name of the run (e.g., "synth_1")

    Returns:
        RunStatus object with current state
    """
    if not run_dir.exists():
        return RunStatus(
            name=run_name,
            state=BuildState.NOT_STARTED,
        )

    timestamp = _get_run_directory_timestamp(run_dir)

    # Check for begin/end markers
    begin_marker = run_dir / ".vivado.begin.rst"
    end_marker = run_dir / ".vivado.end.rst"
    error_marker = run_dir / ".vivado.error.rst"

    # Check runme.log for detailed status
    runme_log = run_dir / "runme.log"
    progress: str | None = None
    status_message: str | None = None

    if runme_log.exists():
        try:
            log_content = runme_log.read_text(errors="replace")

            # Look for progress indicators
            # Vivado logs "Progress: X%" during runs
            progress_matches = re.findall(r"Progress:\s*(\d+%)", log_content)
            if progress_matches:
                progress = progress_matches[-1]  # Get the most recent progress

            # Look for completion status
            if "synth_design Complete!" in log_content:
                status_message = "synth_design Complete!"
            elif "place_design Complete!" in log_content:
                status_message = "place_design Complete!"
            elif "route_design Complete!" in log_content:
                status_message = "route_design Complete!"
            elif "write_bitstream Complete!" in log_content:
                status_message = "write_bitstream Complete!"
            elif "Implementation successful" in log_content:
                status_message = "Implementation successful"
            elif "Synthesis successful" in log_content:
                status_message = "Synthesis successful"

            # Check for error conditions
            if re.search(r"ERROR:\s*\[", log_content):
                return RunStatus(
                    name=run_name,
                    state=BuildState.FAILED,
                    progress=progress,
                    status_message="Build failed with errors",
                    timestamp=timestamp,
                )

        except OSError:
            pass

    # Determine state based on markers and log content
    if error_marker.exists():
        return RunStatus(
            name=run_name,
            state=BuildState.FAILED,
            progress=progress,
            status_message=status_message or "Build failed",
            timestamp=timestamp,
        )

    if end_marker.exists():
        # Run completed (may be success or failure)
        if status_message and "Complete" in status_message:
            return RunStatus(
                name=run_name,
                state=BuildState.COMPLETED,
                progress="100%",
                status_message=status_message,
                timestamp=timestamp,
            )
        # Check if there's a bitstream file for impl runs
        if run_name.startswith("impl"):
            bit_files = list(run_dir.glob("*.bit"))
            if bit_files:
                return RunStatus(
                    name=run_name,
                    state=BuildState.COMPLETED,
                    progress="100%",
                    status_message="Bitstream generated",
                    timestamp=timestamp,
                )
        return RunStatus(
            name=run_name,
            state=BuildState.COMPLETED,
            progress=progress or "100%",
            status_message=status_message,
            timestamp=timestamp,
        )

    if begin_marker.exists() and not end_marker.exists():
        return RunStatus(
            name=run_name,
            state=BuildState.IN_PROGRESS,
            progress=progress,
            status_message=status_message or "Build in progress",
            timestamp=timestamp,
        )

    # If we have some files but no markers, it might be an incomplete or old run
    if runme_log.exists():
        return RunStatus(
            name=run_name,
            state=BuildState.FAILED,
            progress=progress,
            status_message="Run incomplete or interrupted",
            timestamp=timestamp,
        )

    return RunStatus(
        name=run_name,
        state=BuildState.NOT_STARTED,
        timestamp=timestamp,
    )


def get_build_status(project_path: str | Path) -> BuildStatus:
    """Get the current build status of a Vivado project.

    Reads Vivado run status from the .runs directory to determine if
    a previous build completed successfully, is in progress, or failed.

    Args:
        project_path: Path to the Vivado project file (.xpr) or project directory

    Returns:
        BuildStatus object containing the overall state and individual run statuses
    """
    path = Path(project_path)

    # If it's a file, use the parent directory
    if path.is_file():
        project_dir = path.parent
    else:
        project_dir = path

    # Find the project name for the runs directory
    # Vivado creates runs directories like: <project_name>.runs/
    runs_dir: Path | None = None

    # First try to find .xpr file to get exact project name
    xpr_files = list(project_dir.glob("*.xpr"))
    if xpr_files:
        project_name = xpr_files[0].stem
        runs_dir = project_dir / f"{project_name}.runs"

    # Fallback: look for any .runs directory
    if runs_dir is None or not runs_dir.exists():
        runs_dirs = list(project_dir.glob("*.runs"))
        if runs_dirs:
            runs_dir = runs_dirs[0]

    # If no runs directory exists, build hasn't been started
    if runs_dir is None or not runs_dir.exists():
        return BuildStatus(
            project_path=str(project_path),
            overall_state=BuildState.NOT_STARTED,
            runs_directory_exists=False,
        )

    # Parse synthesis run status
    synth_dir = runs_dir / "synth_1"
    synth_status = _parse_run_status(synth_dir, "synth_1")

    # Parse implementation run status
    impl_dir = runs_dir / "impl_1"
    impl_status = _parse_run_status(impl_dir, "impl_1")

    # Get the most recent timestamp
    timestamps = [
        synth_status.timestamp,
        impl_status.timestamp,
    ]
    valid_timestamps = [t for t in timestamps if t is not None]
    last_timestamp = max(valid_timestamps) if valid_timestamps else None

    # Determine overall state
    # Priority: in_progress > failed > completed > not_started
    if synth_status.state == BuildState.IN_PROGRESS or impl_status.state == BuildState.IN_PROGRESS:
        overall_state = BuildState.IN_PROGRESS
    elif synth_status.state == BuildState.FAILED or impl_status.state == BuildState.FAILED:
        overall_state = BuildState.FAILED
    elif impl_status.state == BuildState.COMPLETED:
        overall_state = BuildState.COMPLETED
    elif synth_status.state == BuildState.COMPLETED:
        # Synthesis done but implementation not complete
        overall_state = BuildState.COMPLETED
    else:
        overall_state = BuildState.NOT_STARTED

    return BuildStatus(
        project_path=str(project_path),
        overall_state=overall_state,
        synthesis=synth_status,
        implementation=impl_status,
        last_build_timestamp=last_timestamp,
        runs_directory_exists=True,
    )


async def run_synthesis(
    project_path: str | Path,
    vivado_install: VivadoInstallation | None = None,
    timeout: int | None = None,
) -> BuildResult:
    """Run Vivado synthesis only.

    This runs only the synthesis step in batch mode, allowing quick checking
    for synthesis errors without running the full build flow.

    Args:
        project_path: Path to the Vivado project (.xpr) or TCL script (.tcl)
        vivado_install: Optional specific Vivado installation to use.
                       If None, auto-detects the installation.
        timeout: Optional timeout in seconds for the synthesis process.

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

    # Generate the synthesis TCL script
    synth_tcl = _generate_synthesis_tcl(validated_path)

    # Write TCL script to a temporary file
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".tcl",
        delete=False,
        prefix="vivado_synth_",
    ) as tcl_file:
        tcl_file.write(synth_tcl)
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
                    message=f"Synthesis timed out after {timeout} seconds",
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


async def run_implementation(
    project_path: str | Path,
    vivado_install: VivadoInstallation | None = None,
    timeout: int | None = None,
) -> BuildResult:
    """Run Vivado implementation only (after synthesis is complete).

    This runs only the implementation step (place and route) plus bitstream
    generation in batch mode, allowing testing of place and route without
    regenerating synthesis.

    Requires that synthesis has already been completed successfully.

    Args:
        project_path: Path to the Vivado project (.xpr) or TCL script (.tcl)
        vivado_install: Optional specific Vivado installation to use.
                       If None, auto-detects the installation.
        timeout: Optional timeout in seconds for the implementation process.

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

    # For .xpr projects, verify synthesis is complete before running implementation
    if validated_path.suffix.lower() == ".xpr":
        build_status = get_build_status(validated_path)
        if build_status.synthesis is None or build_status.synthesis.state != BuildState.COMPLETED:
            return BuildResult(
                success=False,
                project_path=str(validated_path),
                vivado_version="unknown",
                errors=[BuildMessage(
                    severity="ERROR",
                    id="vivado-mcp",
                    message="Synthesis not complete. Run synthesis before implementation.",
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

    # Generate the implementation TCL script
    impl_tcl = _generate_implementation_tcl(validated_path)

    # Write TCL script to a temporary file
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".tcl",
        delete=False,
        prefix="vivado_impl_",
    ) as tcl_file:
        tcl_file.write(impl_tcl)
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
                    message=f"Implementation timed out after {timeout} seconds",
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


# Pattern to parse bitstream file path from Vivado output
_BITSTREAM_PATH_PATTERN = re.compile(r"^BITSTREAM_FILE:\s*(.+)$", re.MULTILINE)


def _parse_bitstream_path(output: str) -> str | None:
    """Parse the bitstream file path from Vivado output.

    The TCL script outputs a line "BITSTREAM_FILE: /path/to/file.bit"
    which we parse to extract the path.

    Args:
        output: The stdout/stderr output from Vivado

    Returns:
        The bitstream file path if found, None otherwise
    """
    match = _BITSTREAM_PATH_PATTERN.search(output)
    if match:
        return match.group(1).strip()
    return None


def _find_bitstream_file(project_path: Path) -> str | None:
    """Find the bitstream file in the implementation run directory.

    Args:
        project_path: Path to the project file (.xpr)

    Returns:
        Path to the bitstream file if found, None otherwise
    """
    project_dir = project_path.parent
    project_name = project_path.stem

    # Look in the standard impl_1 directory
    impl_dir = project_dir / f"{project_name}.runs" / "impl_1"
    if impl_dir.exists():
        bit_files = list(impl_dir.glob("*.bit"))
        if bit_files:
            return str(bit_files[0])

    return None


async def run_bitstream_generation(
    project_path: str | Path,
    vivado_install: VivadoInstallation | None = None,
    timeout: int | None = None,
) -> BitstreamResult:
    """Generate bitstream only (after implementation is complete).

    This runs only the bitstream generation step in batch mode, allowing
    regeneration of the bitstream without re-running implementation.

    Requires that implementation has already been completed successfully.

    Args:
        project_path: Path to the Vivado project (.xpr) or TCL script (.tcl)
        vivado_install: Optional specific Vivado installation to use.
                       If None, auto-detects the installation.
        timeout: Optional timeout in seconds for the bitstream generation process.

    Returns:
        BitstreamResult containing success status, bitstream path, errors, and warnings
    """
    # Validate project path
    validated_path, error = _validate_project_path(project_path)
    if error:
        return BitstreamResult(
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

    # For .xpr projects, verify implementation is complete before generating bitstream
    if validated_path.suffix.lower() == ".xpr":
        build_status = get_build_status(validated_path)
        impl_state = build_status.implementation
        if impl_state is None or impl_state.state != BuildState.COMPLETED:
            return BitstreamResult(
                success=False,
                project_path=str(validated_path),
                vivado_version="unknown",
                errors=[BuildMessage(
                    severity="ERROR",
                    id="vivado-mcp",
                    message=(
                        "Implementation not complete. "
                        "Run implementation before generating bitstream."
                    ),
                )],
                exit_code=-1,
            )

    # Get Vivado installation
    if vivado_install is None:
        vivado_install = get_default_vivado()

    if vivado_install is None:
        return BitstreamResult(
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

    # Generate the bitstream TCL script
    bitstream_tcl = _generate_bitstream_tcl(validated_path)

    # Write TCL script to a temporary file
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".tcl",
        delete=False,
        prefix="vivado_bitstream_",
    ) as tcl_file:
        tcl_file.write(bitstream_tcl)
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
            return BitstreamResult(
                success=False,
                project_path=str(validated_path),
                vivado_version=vivado_install.version,
                errors=[BuildMessage(
                    severity="ERROR",
                    id="vivado-mcp",
                    message=f"Bitstream generation timed out after {timeout} seconds",
                )],
                exit_code=-1,
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        # Parse output for errors and warnings
        combined_output = stdout + "\n" + stderr
        errors, critical_warnings = parse_vivado_output(combined_output)

        # Parse bitstream file path from output
        bitstream_path = _parse_bitstream_path(combined_output)

        # If not found in output, try to find it in the impl directory
        if bitstream_path is None and validated_path.suffix.lower() == ".xpr":
            bitstream_path = _find_bitstream_file(validated_path)

        # Determine success
        exit_code = process.returncode or 0
        success = exit_code == 0 and len(errors) == 0

        return BitstreamResult(
            success=success,
            project_path=str(validated_path),
            vivado_version=vivado_install.version,
            bitstream_path=bitstream_path,
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
