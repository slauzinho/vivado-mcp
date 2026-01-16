"""Vivado persistent TCL shell session module.

This module provides functionality to maintain a persistent Vivado TCL shell
session, allowing subsequent commands to run faster without Vivado startup overhead.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from vivado_mcp.vivado.build import BuildMessage, parse_vivado_output
from vivado_mcp.vivado.detection import VivadoInstallation, get_default_vivado

if TYPE_CHECKING:
    pass


class SessionState(str, Enum):
    """Represents the state of a TCL shell session."""

    STARTING = "starting"
    READY = "ready"
    BUSY = "busy"
    CLOSED = "closed"
    ERROR = "error"


@dataclass
class TclCommandResult:
    """Represents the result of a TCL command execution."""

    success: bool
    command: str
    output: str
    errors: list[BuildMessage] = field(default_factory=list)
    critical_warnings: list[BuildMessage] = field(default_factory=list)
    execution_time_ms: float = 0.0

    def to_dict(self) -> dict[str, object]:
        """Convert to dictionary for JSON serialization."""
        return {
            "success": self.success,
            "command": self.command,
            "output": self.output,
            "errors": [e.to_dict() for e in self.errors],
            "critical_warnings": [w.to_dict() for w in self.critical_warnings],
            "error_count": len(self.errors),
            "critical_warning_count": len(self.critical_warnings),
            "execution_time_ms": self.execution_time_ms,
        }


@dataclass
class SessionInfo:
    """Information about a TCL shell session."""

    session_id: str
    state: SessionState
    vivado_version: str
    started_at: str
    working_directory: str | None = None
    command_count: int = 0

    def to_dict(self) -> dict[str, object]:
        """Convert to dictionary for JSON serialization."""
        return {
            "session_id": self.session_id,
            "state": self.state.value,
            "vivado_version": self.vivado_version,
            "started_at": self.started_at,
            "working_directory": self.working_directory,
            "command_count": self.command_count,
        }


class TclSession:
    """Manages a persistent Vivado TCL shell session.

    This class maintains a running Vivado process in TCL mode, allowing
    commands to be executed without the startup overhead of launching
    Vivado for each command.
    """

    # Unique marker to detect end of command output
    _OUTPUT_MARKER = "<<<VIVADO_MCP_CMD_COMPLETE>>>"
    _ERROR_MARKER = "<<<VIVADO_MCP_CMD_ERROR>>>"

    def __init__(
        self,
        vivado_install: VivadoInstallation | None = None,
        working_directory: str | Path | None = None,
    ) -> None:
        """Initialize a TCL session.

        Args:
            vivado_install: Optional specific Vivado installation to use.
                           If None, auto-detects the installation.
            working_directory: Optional working directory for the session.
        """
        self._vivado_install = vivado_install
        self._working_directory = Path(working_directory) if working_directory else None
        self._process: asyncio.subprocess.Process | None = None
        self._state = SessionState.CLOSED
        self._session_id = str(uuid.uuid4())
        self._started_at: str | None = None
        self._command_count = 0
        self._lock = asyncio.Lock()

    @property
    def session_id(self) -> str:
        """Get the unique session ID."""
        return self._session_id

    @property
    def state(self) -> SessionState:
        """Get the current session state."""
        return self._state

    @property
    def is_active(self) -> bool:
        """Check if the session is active and ready for commands."""
        return self._state in (SessionState.READY, SessionState.BUSY)

    def get_info(self) -> SessionInfo:
        """Get information about the session."""
        return SessionInfo(
            session_id=self._session_id,
            state=self._state,
            vivado_version=self._vivado_install.version if self._vivado_install else "unknown",
            started_at=self._started_at or "",
            working_directory=str(self._working_directory) if self._working_directory else None,
            command_count=self._command_count,
        )

    async def start(self) -> tuple[bool, str]:
        """Start the Vivado TCL shell session.

        Returns:
            Tuple of (success, message)
        """
        async with self._lock:
            if self._state in (SessionState.READY, SessionState.BUSY):
                return False, "Session is already running"

            self._state = SessionState.STARTING

            # Get Vivado installation if not provided
            if self._vivado_install is None:
                self._vivado_install = get_default_vivado()

            if self._vivado_install is None:
                self._state = SessionState.ERROR
                return False, "No Vivado installation found. Install Vivado or set VIVADO_PATH."

            # Build command for TCL mode
            vivado_exe = str(self._vivado_install.executable)
            cmd = [
                vivado_exe,
                "-mode", "tcl",
                "-nojournal",
                "-nolog",
            ]

            try:
                # Start the process
                self._process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=self._working_directory,
                )

                self._started_at = datetime.now().isoformat()

                # Wait for Vivado to start and show the prompt
                # We read until we see "Vivado%" or timeout
                startup_output = await self._read_until_prompt(timeout=60.0)

                if "Vivado%" not in startup_output and "vivado%" not in startup_output.lower():
                    # Check if process is still running
                    if self._process.returncode is not None:
                        self._state = SessionState.ERROR
                        return False, f"Vivado process exited during startup: {startup_output}"

                self._state = SessionState.READY
                return True, f"Session started with Vivado {self._vivado_install.version}"

            except FileNotFoundError:
                self._state = SessionState.ERROR
                return False, f"Vivado executable not found: {vivado_exe}"
            except asyncio.TimeoutError:
                self._state = SessionState.ERROR
                await self._kill_process()
                return False, "Timeout waiting for Vivado to start"
            except OSError as e:
                self._state = SessionState.ERROR
                return False, f"Failed to start Vivado: {e}"

    async def _read_until_prompt(self, timeout: float = 30.0) -> str:
        """Read output until we see a Vivado prompt or marker.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            The collected output
        """
        if self._process is None or self._process.stdout is None:
            return ""

        output_parts: list[str] = []
        start_time = asyncio.get_event_loop().time()

        while True:
            remaining_time = timeout - (asyncio.get_event_loop().time() - start_time)
            if remaining_time <= 0:
                break

            try:
                # Read available data with timeout
                chunk = await asyncio.wait_for(
                    self._process.stdout.read(4096),
                    timeout=min(remaining_time, 1.0),
                )

                if not chunk:
                    # EOF - process may have exited
                    break

                text = chunk.decode("utf-8", errors="replace")
                output_parts.append(text)

                # Check for our completion markers
                combined = "".join(output_parts)
                if self._OUTPUT_MARKER in combined or self._ERROR_MARKER in combined:
                    break

                # Check for Vivado prompt (for startup)
                if "Vivado%" in combined or "vivado%" in combined.lower():
                    # Wait a tiny bit more for any trailing output
                    try:
                        extra = await asyncio.wait_for(
                            self._process.stdout.read(1024),
                            timeout=0.1,
                        )
                        if extra:
                            output_parts.append(extra.decode("utf-8", errors="replace"))
                    except asyncio.TimeoutError:
                        pass
                    break

            except asyncio.TimeoutError:
                # Check if we have a prompt in accumulated output
                combined = "".join(output_parts)
                if "Vivado%" in combined or "vivado%" in combined.lower():
                    break
                # Otherwise continue waiting

        return "".join(output_parts)

    async def execute(
        self,
        command: str,
        timeout: float = 300.0,
    ) -> TclCommandResult:
        """Execute a TCL command in the session.

        Args:
            command: The TCL command to execute
            timeout: Maximum time to wait for command completion in seconds

        Returns:
            TclCommandResult with the command output and any errors
        """
        async with self._lock:
            if self._state == SessionState.CLOSED:
                return TclCommandResult(
                    success=False,
                    command=command,
                    output="Session is not started. Call start() first.",
                )

            if self._state == SessionState.ERROR:
                return TclCommandResult(
                    success=False,
                    command=command,
                    output="Session is in error state. Close and restart.",
                )

            if self._process is None or self._process.stdin is None:
                return TclCommandResult(
                    success=False,
                    command=command,
                    output="Session process is not available.",
                )

            self._state = SessionState.BUSY
            start_time = asyncio.get_event_loop().time()

            try:
                # Wrap command with markers for output detection
                # We use puts to print a marker after the command completes
                wrapped_command = f"""
if {{[catch {{{command}}} result]}} {{
    puts $result
    puts "{self._ERROR_MARKER}"
}} else {{
    if {{$result ne ""}} {{
        puts $result
    }}
    puts "{self._OUTPUT_MARKER}"
}}
"""
                # Send the command
                self._process.stdin.write(wrapped_command.encode("utf-8"))
                await self._process.stdin.drain()

                # Read output until we see our marker
                output = await self._read_until_prompt(timeout=timeout)

                end_time = asyncio.get_event_loop().time()
                execution_time_ms = (end_time - start_time) * 1000

                self._command_count += 1

                # Check for error marker
                is_error = self._ERROR_MARKER in output
                success = not is_error

                # Clean up markers from output
                clean_output = output.replace(self._OUTPUT_MARKER, "").replace(
                    self._ERROR_MARKER, ""
                )
                # Remove empty lines and the command echo
                lines = clean_output.strip().split("\n")
                # Filter out command echo and prompt lines
                filtered_lines = [
                    line
                    for line in lines
                    if not line.strip().startswith("Vivado%")
                    and not line.strip() == command.strip()
                    and line.strip()
                ]
                clean_output = "\n".join(filtered_lines)

                # Parse for errors and warnings
                errors, critical_warnings = parse_vivado_output(output)

                if errors:
                    success = False

                self._state = SessionState.READY
                return TclCommandResult(
                    success=success,
                    command=command,
                    output=clean_output,
                    errors=errors,
                    critical_warnings=critical_warnings,
                    execution_time_ms=execution_time_ms,
                )

            except asyncio.TimeoutError:
                self._state = SessionState.ERROR
                return TclCommandResult(
                    success=False,
                    command=command,
                    output=f"Command timed out after {timeout} seconds",
                )

            except BrokenPipeError:
                self._state = SessionState.ERROR
                return TclCommandResult(
                    success=False,
                    command=command,
                    output="Session process has terminated unexpectedly",
                )

    async def close(self) -> tuple[bool, str]:
        """Close the TCL session.

        Returns:
            Tuple of (success, message)
        """
        async with self._lock:
            if self._state == SessionState.CLOSED:
                return True, "Session is already closed"

            if self._process is None:
                self._state = SessionState.CLOSED
                return True, "Session closed"

            try:
                # Try to exit gracefully first
                if self._process.stdin is not None:
                    try:
                        self._process.stdin.write(b"exit\n")
                        await self._process.stdin.drain()

                        # Wait briefly for graceful exit
                        try:
                            await asyncio.wait_for(self._process.wait(), timeout=5.0)
                        except asyncio.TimeoutError:
                            pass
                    except (BrokenPipeError, OSError):
                        pass

                # Kill if still running
                await self._kill_process()

                self._state = SessionState.CLOSED
                return True, "Session closed successfully"

            except Exception as e:
                self._state = SessionState.CLOSED
                return False, f"Error closing session: {e}"

    async def _kill_process(self) -> None:
        """Kill the Vivado process if running."""
        if self._process is not None:
            try:
                if self._process.returncode is None:
                    self._process.kill()
                    await self._process.wait()
            except ProcessLookupError:
                pass  # Process already exited
            finally:
                self._process = None


# Global session manager for managing multiple sessions
class SessionManager:
    """Manages multiple TCL shell sessions."""

    def __init__(self) -> None:
        """Initialize the session manager."""
        self._sessions: dict[str, TclSession] = {}
        self._default_session_id: str | None = None
        self._lock = asyncio.Lock()

    async def create_session(
        self,
        vivado_install: VivadoInstallation | None = None,
        working_directory: str | Path | None = None,
        set_as_default: bool = True,
    ) -> tuple[TclSession, bool, str]:
        """Create a new TCL session.

        Args:
            vivado_install: Optional specific Vivado installation to use
            working_directory: Optional working directory for the session
            set_as_default: If True, sets this as the default session

        Returns:
            Tuple of (session, success, message)
        """
        async with self._lock:
            session = TclSession(
                vivado_install=vivado_install,
                working_directory=working_directory,
            )

            success, message = await session.start()

            if success:
                self._sessions[session.session_id] = session
                if set_as_default or self._default_session_id is None:
                    self._default_session_id = session.session_id

            return session, success, message

    def get_session(self, session_id: str | None = None) -> TclSession | None:
        """Get a session by ID or return the default session.

        Args:
            session_id: Optional session ID. If None, returns default session.

        Returns:
            The requested session or None if not found
        """
        if session_id is None:
            session_id = self._default_session_id

        if session_id is None:
            return None

        return self._sessions.get(session_id)

    async def close_session(self, session_id: str | None = None) -> tuple[bool, str]:
        """Close a session.

        Args:
            session_id: Optional session ID. If None, closes default session.

        Returns:
            Tuple of (success, message)
        """
        async with self._lock:
            if session_id is None:
                session_id = self._default_session_id

            if session_id is None:
                return False, "No session to close"

            session = self._sessions.get(session_id)
            if session is None:
                return False, f"Session not found: {session_id}"

            success, message = await session.close()

            # Remove from sessions dict
            del self._sessions[session_id]

            # Update default session
            if self._default_session_id == session_id:
                self._default_session_id = (
                    next(iter(self._sessions.keys())) if self._sessions else None
                )

            return success, message

    async def close_all_sessions(self) -> list[tuple[str, bool, str]]:
        """Close all sessions.

        Returns:
            List of (session_id, success, message) tuples
        """
        results: list[tuple[str, bool, str]] = []

        async with self._lock:
            for session_id in list(self._sessions.keys()):
                session = self._sessions[session_id]
                success, message = await session.close()
                results.append((session_id, success, message))
                del self._sessions[session_id]

            self._default_session_id = None

        return results

    def list_sessions(self) -> list[SessionInfo]:
        """List all active sessions.

        Returns:
            List of SessionInfo objects
        """
        return [session.get_info() for session in self._sessions.values()]

    @property
    def default_session_id(self) -> str | None:
        """Get the default session ID."""
        return self._default_session_id


# Global session manager instance
_session_manager: SessionManager | None = None


def get_session_manager() -> SessionManager:
    """Get the global session manager instance."""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager


async def run_tcl_command_with_fallback(
    command: str,
    session_id: str | None = None,
    vivado_install: VivadoInstallation | None = None,
    timeout: float = 300.0,
) -> TclCommandResult:
    """Run a TCL command, falling back to batch mode if session unavailable.

    This function first tries to use an existing session. If no session
    is available, it falls back to running the command in batch mode.

    Args:
        command: The TCL command to execute
        session_id: Optional session ID to use
        vivado_install: Optional Vivado installation for batch fallback
        timeout: Maximum time to wait for command completion

    Returns:
        TclCommandResult with the command output
    """
    manager = get_session_manager()
    session = manager.get_session(session_id)

    if session is not None and session.is_active:
        # Use existing session
        return await session.execute(command, timeout=timeout)

    # Fall back to batch mode
    return await _run_batch_command(command, vivado_install, timeout)


async def _run_batch_command(
    command: str,
    vivado_install: VivadoInstallation | None = None,
    timeout: float = 300.0,
) -> TclCommandResult:
    """Run a single TCL command in batch mode.

    Args:
        command: The TCL command to execute
        vivado_install: Optional Vivado installation to use
        timeout: Maximum time to wait in seconds

    Returns:
        TclCommandResult with the command output
    """
    import tempfile

    if vivado_install is None:
        vivado_install = get_default_vivado()

    if vivado_install is None:
        return TclCommandResult(
            success=False,
            command=command,
            output="No Vivado installation found. Install Vivado or set VIVADO_PATH.",
        )

    # Create a temporary TCL file with the command
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".tcl",
        delete=False,
        prefix="vivado_cmd_",
    ) as tcl_file:
        tcl_file.write(command)
        tcl_file.write("\nexit\n")
        tcl_path = tcl_file.name

    start_time = asyncio.get_event_loop().time()

    try:
        vivado_exe = str(vivado_install.executable)
        cmd = [
            vivado_exe,
            "-mode", "batch",
            "-source", tcl_path,
            "-nojournal",
            "-nolog",
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return TclCommandResult(
                success=False,
                command=command,
                output=f"Command timed out after {timeout} seconds",
            )

        end_time = asyncio.get_event_loop().time()
        execution_time_ms = (end_time - start_time) * 1000

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        combined_output = stdout + "\n" + stderr

        errors, critical_warnings = parse_vivado_output(combined_output)

        exit_code = process.returncode or 0
        success = exit_code == 0 and len(errors) == 0

        return TclCommandResult(
            success=success,
            command=command,
            output=stdout.strip(),
            errors=errors,
            critical_warnings=critical_warnings,
            execution_time_ms=execution_time_ms,
        )

    finally:
        import os

        try:
            os.unlink(tcl_path)
        except OSError:
            pass
