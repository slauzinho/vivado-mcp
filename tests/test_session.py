"""Tests for Vivado persistent TCL shell session module."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vivado_mcp.vivado.detection import VivadoInstallation
from vivado_mcp.vivado.session import (
    SessionInfo,
    SessionManager,
    SessionState,
    TclCommandResult,
    TclSession,
    _run_batch_command,
    get_session_manager,
    run_tcl_command_with_fallback,
)


class TestTclCommandResult:
    """Tests for TclCommandResult dataclass."""

    def test_to_dict_success(self) -> None:
        result = TclCommandResult(
            success=True,
            command="puts hello",
            output="hello",
            execution_time_ms=50.5,
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["command"] == "puts hello"
        assert d["output"] == "hello"
        assert d["errors"] == []
        assert d["critical_warnings"] == []
        assert d["error_count"] == 0
        assert d["critical_warning_count"] == 0
        assert d["execution_time_ms"] == 50.5

    def test_to_dict_with_errors(self) -> None:
        from vivado_mcp.vivado.build import BuildMessage

        error = BuildMessage(
            severity="ERROR",
            id="Synth 8-87",
            message="Signal not found",
        )
        result = TclCommandResult(
            success=False,
            command="synth_design",
            output="Error output",
            errors=[error],
        )
        d = result.to_dict()
        assert d["success"] is False
        assert d["error_count"] == 1
        assert len(d["errors"]) == 1  # type: ignore[arg-type]


class TestSessionInfo:
    """Tests for SessionInfo dataclass."""

    def test_to_dict(self) -> None:
        info = SessionInfo(
            session_id="test-id-123",
            state=SessionState.READY,
            vivado_version="2023.2",
            started_at="2024-01-15T10:30:00",
            working_directory="/path/to/project",
            command_count=5,
        )
        d = info.to_dict()
        assert d["session_id"] == "test-id-123"
        assert d["state"] == "ready"
        assert d["vivado_version"] == "2023.2"
        assert d["started_at"] == "2024-01-15T10:30:00"
        assert d["working_directory"] == "/path/to/project"
        assert d["command_count"] == 5


class TestSessionState:
    """Tests for SessionState enum."""

    def test_values(self) -> None:
        assert SessionState.STARTING.value == "starting"
        assert SessionState.READY.value == "ready"
        assert SessionState.BUSY.value == "busy"
        assert SessionState.CLOSED.value == "closed"
        assert SessionState.ERROR.value == "error"


class TestTclSession:
    """Tests for TclSession class."""

    def test_init_defaults(self) -> None:
        session = TclSession()
        assert session.state == SessionState.CLOSED
        assert session.is_active is False
        assert session.session_id is not None
        assert len(session.session_id) == 36  # UUID format

    def test_init_with_working_directory(self, tmp_path: Path) -> None:
        session = TclSession(working_directory=tmp_path)
        info = session.get_info()
        assert info.working_directory == str(tmp_path)

    def test_init_with_vivado_install(self, tmp_path: Path) -> None:
        install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )
        session = TclSession(vivado_install=install)
        info = session.get_info()
        assert info.vivado_version == "2023.2"

    def test_get_info_closed_session(self) -> None:
        session = TclSession()
        info = session.get_info()
        assert info.state == SessionState.CLOSED
        assert info.command_count == 0
        assert info.started_at == ""

    def test_is_active_states(self) -> None:
        session = TclSession()
        # Initially closed
        assert session.is_active is False

        # Test with different states
        session._state = SessionState.READY
        assert session.is_active is True

        session._state = SessionState.BUSY
        assert session.is_active is True

        session._state = SessionState.STARTING
        assert session.is_active is False

        session._state = SessionState.ERROR
        assert session.is_active is False

        session._state = SessionState.CLOSED
        assert session.is_active is False

    @pytest.mark.asyncio
    async def test_start_no_vivado_found(self) -> None:
        """Test starting session when no Vivado is installed."""
        session = TclSession()

        with patch(
            "vivado_mcp.vivado.session.get_default_vivado",
            return_value=None,
        ):
            success, message = await session.start()
            assert success is False
            assert "No Vivado installation found" in message
            assert session.state == SessionState.ERROR

    @pytest.mark.asyncio
    async def test_start_already_running(self, tmp_path: Path) -> None:
        """Test starting session when already running."""
        install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )
        session = TclSession(vivado_install=install)
        session._state = SessionState.READY

        success, message = await session.start()
        assert success is False
        assert "already running" in message

    @pytest.mark.asyncio
    async def test_start_vivado_not_found(self, tmp_path: Path) -> None:
        """Test starting session when Vivado executable doesn't exist."""
        install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "nonexistent" / "vivado",
        )
        session = TclSession(vivado_install=install)

        success, message = await session.start()
        assert success is False
        assert "not found" in message.lower()
        assert session.state == SessionState.ERROR

    @pytest.mark.asyncio
    async def test_start_success(self, tmp_path: Path) -> None:
        """Test successful session start."""
        install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )
        session = TclSession(vivado_install=install)

        # Mock subprocess
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.stdin = MagicMock()
        mock_process.stdout = MagicMock()

        # Mock stdout.read to return startup prompt
        async def mock_read(n: int) -> bytes:
            return b"Vivado% "

        mock_process.stdout.read = mock_read

        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            success, message = await session.start()
            assert success is True
            assert "2023.2" in message
            assert session.state == SessionState.READY
            assert session.is_active is True

    @pytest.mark.asyncio
    async def test_execute_not_started(self) -> None:
        """Test executing command when session is not started."""
        session = TclSession()

        result = await session.execute("puts hello")
        assert result.success is False
        assert "not started" in result.output

    @pytest.mark.asyncio
    async def test_execute_error_state(self) -> None:
        """Test executing command when session is in error state."""
        session = TclSession()
        session._state = SessionState.ERROR

        result = await session.execute("puts hello")
        assert result.success is False
        assert "error state" in result.output

    @pytest.mark.asyncio
    async def test_execute_success(self, tmp_path: Path) -> None:
        """Test successful command execution."""
        install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )
        session = TclSession(vivado_install=install)
        session._state = SessionState.READY

        # Mock process and I/O
        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock()

        mock_stdout = MagicMock()
        output_marker = TclSession._OUTPUT_MARKER

        async def mock_read(n: int) -> bytes:
            return f"hello\n{output_marker}\nVivado% ".encode()

        mock_stdout.read = mock_read

        mock_process = MagicMock()
        mock_process.stdin = mock_stdin
        mock_process.stdout = mock_stdout

        session._process = mock_process

        result = await session.execute("puts hello")
        assert result.success is True
        assert "hello" in result.output
        assert result.execution_time_ms > 0

    @pytest.mark.asyncio
    async def test_execute_with_error_output(self, tmp_path: Path) -> None:
        """Test command execution with error output."""
        install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )
        session = TclSession(vivado_install=install)
        session._state = SessionState.READY

        # Mock process and I/O
        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock()

        mock_stdout = MagicMock()
        error_marker = TclSession._ERROR_MARKER

        async def mock_read(n: int) -> bytes:
            return f"ERROR: [Synth 8-87] Signal not found\n{error_marker}\nVivado% ".encode()

        mock_stdout.read = mock_read

        mock_process = MagicMock()
        mock_process.stdin = mock_stdin
        mock_process.stdout = mock_stdout

        session._process = mock_process

        result = await session.execute("synth_design")
        assert result.success is False
        assert len(result.errors) == 1
        assert result.errors[0].id == "Synth 8-87"

    @pytest.mark.asyncio
    async def test_close_not_started(self) -> None:
        """Test closing session that was never started."""
        session = TclSession()

        success, message = await session.close()
        assert success is True
        assert "already closed" in message

    @pytest.mark.asyncio
    async def test_close_running_session(self, tmp_path: Path) -> None:
        """Test closing a running session."""
        install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )
        session = TclSession(vivado_install=install)
        session._state = SessionState.READY

        # Mock process
        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock()

        mock_process = MagicMock()
        mock_process.stdin = mock_stdin
        mock_process.returncode = None
        mock_process.kill = MagicMock()
        mock_process.wait = AsyncMock()

        session._process = mock_process

        success, message = await session.close()
        assert success is True
        assert "successfully" in message
        assert session.state == SessionState.CLOSED


class TestSessionManager:
    """Tests for SessionManager class."""

    def test_init(self) -> None:
        manager = SessionManager()
        assert manager.default_session_id is None
        assert manager.list_sessions() == []

    @pytest.mark.asyncio
    async def test_create_session_no_vivado(self) -> None:
        """Test creating session when Vivado is not available."""
        manager = SessionManager()

        with patch(
            "vivado_mcp.vivado.session.get_default_vivado",
            return_value=None,
        ):
            session, success, message = await manager.create_session()
            assert success is False
            assert "No Vivado installation found" in message

    @pytest.mark.asyncio
    async def test_create_session_success(self, tmp_path: Path) -> None:
        """Test successful session creation."""
        manager = SessionManager()

        install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )

        # Mock subprocess
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.stdin = MagicMock()
        mock_process.stdout = MagicMock()

        async def mock_read(n: int) -> bytes:
            return b"Vivado% "

        mock_process.stdout.read = mock_read

        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            session, success, message = await manager.create_session(
                vivado_install=install
            )
            assert success is True
            assert session.state == SessionState.READY
            assert manager.default_session_id == session.session_id
            assert len(manager.list_sessions()) == 1

    def test_get_session_none(self) -> None:
        manager = SessionManager()
        assert manager.get_session() is None
        assert manager.get_session("nonexistent") is None

    @pytest.mark.asyncio
    async def test_close_session_none(self) -> None:
        manager = SessionManager()
        success, message = await manager.close_session()
        assert success is False
        assert "No session" in message

    @pytest.mark.asyncio
    async def test_close_session_not_found(self) -> None:
        manager = SessionManager()
        success, message = await manager.close_session("nonexistent-id")
        assert success is False
        assert "not found" in message

    @pytest.mark.asyncio
    async def test_close_all_sessions(self, tmp_path: Path) -> None:
        """Test closing all sessions."""
        manager = SessionManager()

        # Create mock sessions manually
        session1 = TclSession()
        session1._state = SessionState.READY
        session1._process = MagicMock()
        session1._process.returncode = None
        session1._process.stdin = MagicMock()
        session1._process.stdin.write = MagicMock()
        session1._process.stdin.drain = AsyncMock()
        session1._process.kill = MagicMock()
        session1._process.wait = AsyncMock()

        session2 = TclSession()
        session2._state = SessionState.READY
        session2._process = MagicMock()
        session2._process.returncode = None
        session2._process.stdin = MagicMock()
        session2._process.stdin.write = MagicMock()
        session2._process.stdin.drain = AsyncMock()
        session2._process.kill = MagicMock()
        session2._process.wait = AsyncMock()

        manager._sessions[session1.session_id] = session1
        manager._sessions[session2.session_id] = session2
        manager._default_session_id = session1.session_id

        results = await manager.close_all_sessions()
        assert len(results) == 2
        assert all(r[1] for r in results)  # All should succeed
        assert manager.default_session_id is None
        assert len(manager.list_sessions()) == 0

    def test_list_sessions(self) -> None:
        manager = SessionManager()

        # Add a mock session
        session = TclSession()
        session._state = SessionState.READY
        session._started_at = "2024-01-15T10:30:00"
        session._vivado_install = VivadoInstallation(
            version="2023.2",
            path=Path("/opt/Vivado/2023.2"),
            executable=Path("/opt/Vivado/2023.2/bin/vivado"),
        )
        manager._sessions[session.session_id] = session

        sessions = manager.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].session_id == session.session_id
        assert sessions[0].state == SessionState.READY


class TestGetSessionManager:
    """Tests for get_session_manager function."""

    def test_singleton(self) -> None:
        # Reset global state for this test
        import vivado_mcp.vivado.session as session_module

        session_module._session_manager = None

        manager1 = get_session_manager()
        manager2 = get_session_manager()
        assert manager1 is manager2


class TestRunBatchCommand:
    """Tests for _run_batch_command function."""

    @pytest.mark.asyncio
    async def test_no_vivado(self) -> None:
        """Test batch command when no Vivado is available."""
        with patch(
            "vivado_mcp.vivado.session.get_default_vivado",
            return_value=None,
        ):
            result = await _run_batch_command("puts hello")
            assert result.success is False
            assert "No Vivado installation found" in result.output

    @pytest.mark.asyncio
    async def test_success(self, tmp_path: Path) -> None:
        """Test successful batch command execution."""
        install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"hello\n", b""))

        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            result = await _run_batch_command("puts hello", vivado_install=install)
            assert result.success is True
            assert "hello" in result.output
            assert result.execution_time_ms > 0

    @pytest.mark.asyncio
    async def test_timeout(self, tmp_path: Path) -> None:
        """Test batch command timeout."""
        install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )

        mock_process = MagicMock()
        mock_process.kill = MagicMock()
        mock_process.wait = AsyncMock()

        async def slow_communicate() -> tuple[bytes, bytes]:
            await asyncio.sleep(10)
            return (b"", b"")

        mock_process.communicate = slow_communicate

        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            result = await _run_batch_command(
                "puts hello", vivado_install=install, timeout=0.1
            )
            assert result.success is False
            assert "timed out" in result.output
            mock_process.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_with_errors(self, tmp_path: Path) -> None:
        """Test batch command with errors in output."""
        install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )

        error_output = b"ERROR: [Synth 8-87] Signal not found\n"
        mock_process = MagicMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(error_output, b""))

        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            result = await _run_batch_command("synth_design", vivado_install=install)
            assert result.success is False
            assert len(result.errors) == 1
            assert result.errors[0].id == "Synth 8-87"


class TestRunTclCommandWithFallback:
    """Tests for run_tcl_command_with_fallback function."""

    @pytest.mark.asyncio
    async def test_uses_session_when_available(self, tmp_path: Path) -> None:
        """Test that active session is used when available."""
        # Reset session manager
        import vivado_mcp.vivado.session as session_module

        session_module._session_manager = None
        manager = get_session_manager()

        # Create a mock session
        session = TclSession()
        session._state = SessionState.READY
        session._vivado_install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )

        # Mock process
        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock()

        mock_stdout = MagicMock()
        output_marker = TclSession._OUTPUT_MARKER

        async def mock_read(n: int) -> bytes:
            return f"hello\n{output_marker}\nVivado% ".encode()

        mock_stdout.read = mock_read

        mock_process = MagicMock()
        mock_process.stdin = mock_stdin
        mock_process.stdout = mock_stdout

        session._process = mock_process

        manager._sessions[session.session_id] = session
        manager._default_session_id = session.session_id

        result = await run_tcl_command_with_fallback("puts hello")
        assert result.success is True
        assert "hello" in result.output

    @pytest.mark.asyncio
    async def test_falls_back_to_batch(self, tmp_path: Path) -> None:
        """Test fallback to batch mode when no session is available."""
        # Reset session manager
        import vivado_mcp.vivado.session as session_module

        session_module._session_manager = None

        install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"hello\n", b""))

        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_process,
        ):
            result = await run_tcl_command_with_fallback(
                "puts hello", vivado_install=install
            )
            assert result.success is True
            assert "hello" in result.output

    @pytest.mark.asyncio
    async def test_specific_session_id(self, tmp_path: Path) -> None:
        """Test using a specific session ID."""
        # Reset session manager
        import vivado_mcp.vivado.session as session_module

        session_module._session_manager = None
        manager = get_session_manager()

        # Create a mock session
        session = TclSession()
        session._state = SessionState.READY
        session._vivado_install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )

        # Mock process
        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock()

        mock_stdout = MagicMock()
        output_marker = TclSession._OUTPUT_MARKER

        async def mock_read(n: int) -> bytes:
            return f"hello\n{output_marker}\nVivado% ".encode()

        mock_stdout.read = mock_read

        mock_process = MagicMock()
        mock_process.stdin = mock_stdin
        mock_process.stdout = mock_stdout

        session._process = mock_process

        manager._sessions[session.session_id] = session

        # Use specific session ID (not default)
        result = await run_tcl_command_with_fallback(
            "puts hello", session_id=session.session_id
        )
        assert result.success is True
        assert "hello" in result.output
