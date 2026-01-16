"""Tests for Vivado build module."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vivado_mcp.vivado.build import (
    BitstreamResult,
    BuildMessage,
    BuildResult,
    BuildState,
    BuildStatus,
    RunStatus,
    _find_bitstream_file,
    _generate_bitstream_tcl,
    _generate_build_tcl,
    _generate_implementation_tcl,
    _generate_synthesis_tcl,
    _get_run_directory_timestamp,
    _parse_bitstream_path,
    _parse_run_status,
    _validate_project_path,
    get_build_status,
    parse_vivado_output,
    run_bitstream_generation,
    run_implementation,
    run_synthesis,
    run_vivado_build,
)
from vivado_mcp.vivado.detection import VivadoInstallation


class TestBuildMessage:
    """Tests for BuildMessage dataclass."""

    def test_to_dict_basic(self) -> None:
        msg = BuildMessage(
            severity="ERROR",
            id="Synth 8-87",
            message="Signal 'clk' not found",
        )
        result = msg.to_dict()
        assert result["severity"] == "ERROR"
        assert result["id"] == "Synth 8-87"
        assert result["message"] == "Signal 'clk' not found"
        assert result["file"] is None
        assert result["line"] is None

    def test_to_dict_with_file_and_line(self) -> None:
        msg = BuildMessage(
            severity="CRITICAL WARNING",
            id="Place 30-876",
            message="Issue in design",
            file="/path/to/design.v",
            line=42,
        )
        result = msg.to_dict()
        assert result["file"] == "/path/to/design.v"
        assert result["line"] == 42


class TestBuildResult:
    """Tests for BuildResult dataclass."""

    def test_to_dict_success(self) -> None:
        result = BuildResult(
            success=True,
            project_path="/path/to/project.xpr",
            vivado_version="2023.2",
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["project_path"] == "/path/to/project.xpr"
        assert d["vivado_version"] == "2023.2"
        assert d["errors"] == []
        assert d["critical_warnings"] == []
        assert d["error_count"] == 0
        assert d["critical_warning_count"] == 0
        assert d["exit_code"] == 0

    def test_to_dict_with_errors(self) -> None:
        error = BuildMessage(
            severity="ERROR",
            id="Synth 8-87",
            message="Signal not found",
        )
        result = BuildResult(
            success=False,
            project_path="/path/to/project.xpr",
            vivado_version="2023.2",
            errors=[error],
            exit_code=1,
        )
        d = result.to_dict()
        assert d["success"] is False
        assert d["error_count"] == 1
        assert len(d["errors"]) == 1  # type: ignore[arg-type]


class TestParseVivadoOutput:
    """Tests for parsing Vivado output."""

    def test_parse_error(self) -> None:
        output = "ERROR: [Synth 8-87] Signal 'clk' is not declared."
        errors, warnings = parse_vivado_output(output)
        assert len(errors) == 1
        assert len(warnings) == 0
        assert errors[0].severity == "ERROR"
        assert errors[0].id == "Synth 8-87"
        assert "Signal 'clk' is not declared" in errors[0].message

    def test_parse_critical_warning(self) -> None:
        output = "CRITICAL WARNING: [Place 30-876] Placement failed for cell."
        errors, warnings = parse_vivado_output(output)
        assert len(errors) == 0
        assert len(warnings) == 1
        assert warnings[0].severity == "CRITICAL WARNING"
        assert warnings[0].id == "Place 30-876"

    def test_parse_regular_warning_ignored(self) -> None:
        # Regular warnings are not captured (only errors and critical warnings)
        output = "WARNING: [DRC RTSTAT-1] No routable loads."
        errors, warnings = parse_vivado_output(output)
        assert len(errors) == 0
        assert len(warnings) == 0

    def test_parse_multiple_messages(self) -> None:
        output = """
INFO: Starting synthesis
ERROR: [Synth 8-87] Signal 'a' not found
WARNING: [DRC RTSTAT-1] Something
CRITICAL WARNING: [Place 30-876] Placement issue
ERROR: [Synth 8-327] Module 'foo' not found
        """
        errors, warnings = parse_vivado_output(output)
        assert len(errors) == 2
        assert len(warnings) == 1
        assert errors[0].id == "Synth 8-87"
        assert errors[1].id == "Synth 8-327"
        assert warnings[0].id == "Place 30-876"

    def test_parse_with_file_reference(self) -> None:
        output = "ERROR: [Synth 8-87] 'design.v' line 42: Signal not declared"
        errors, _ = parse_vivado_output(output)
        assert len(errors) == 1
        assert errors[0].file == "design.v"
        assert errors[0].line == 42

    def test_parse_empty_output(self) -> None:
        errors, warnings = parse_vivado_output("")
        assert len(errors) == 0
        assert len(warnings) == 0


class TestValidateProjectPath:
    """Tests for project path validation."""

    def test_valid_xpr_file(self, tmp_path: Path) -> None:
        project = tmp_path / "test.xpr"
        project.touch()
        path, error = _validate_project_path(project)
        assert error is None
        assert path == project

    def test_valid_tcl_file(self, tmp_path: Path) -> None:
        project = tmp_path / "build.tcl"
        project.touch()
        path, error = _validate_project_path(project)
        assert error is None
        assert path == project

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        project = tmp_path / "nonexistent.xpr"
        path, error = _validate_project_path(project)
        assert error is not None
        assert "not found" in error

    def test_invalid_extension(self, tmp_path: Path) -> None:
        project = tmp_path / "design.v"
        project.touch()
        path, error = _validate_project_path(project)
        assert error is not None
        assert "Invalid project file type" in error

    def test_directory_not_file(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "project.xpr"
        project_dir.mkdir()
        path, error = _validate_project_path(project_dir)
        assert error is not None
        assert "not a file" in error


class TestGenerateBuildTcl:
    """Tests for TCL script generation."""

    def test_xpr_project(self, tmp_path: Path) -> None:
        project = tmp_path / "test.xpr"
        tcl = _generate_build_tcl(project)

        # Verify key commands are present
        assert "open_project" in tcl
        assert "synth_1" in tcl
        assert "impl_1" in tcl
        assert "write_bitstream" in tcl
        assert "batch" not in tcl  # batch is in command line, not TCL

    def test_tcl_project(self, tmp_path: Path) -> None:
        project = tmp_path / "build.tcl"
        tcl = _generate_build_tcl(project)

        # For TCL projects, should source the file
        assert "source" in tcl
        assert "synth_design" in tcl
        assert "opt_design" in tcl
        assert "place_design" in tcl
        assert "route_design" in tcl
        assert "write_bitstream" in tcl


class TestRunVivadoBuild:
    """Tests for the main build function."""

    @pytest.mark.asyncio
    async def test_project_not_found(self, tmp_path: Path) -> None:
        """Test handling of non-existent project file."""
        result = await run_vivado_build(tmp_path / "nonexistent.xpr")
        assert result.success is False
        assert len(result.errors) == 1
        assert "not found" in result.errors[0].message

    @pytest.mark.asyncio
    async def test_invalid_project_type(self, tmp_path: Path) -> None:
        """Test handling of invalid file type."""
        invalid_file = tmp_path / "design.v"
        invalid_file.touch()
        result = await run_vivado_build(invalid_file)
        assert result.success is False
        assert len(result.errors) == 1
        assert "Invalid project file type" in result.errors[0].message

    @pytest.mark.asyncio
    async def test_no_vivado_installation(self, tmp_path: Path) -> None:
        """Test handling when no Vivado is found."""
        project = tmp_path / "test.xpr"
        project.touch()

        with patch(
            "vivado_mcp.vivado.build.get_default_vivado",
            return_value=None,
        ):
            result = await run_vivado_build(project)
            assert result.success is False
            assert len(result.errors) == 1
            assert "No Vivado installation found" in result.errors[0].message

    @pytest.mark.asyncio
    async def test_successful_build(self, tmp_path: Path) -> None:
        """Test successful build execution."""
        project = tmp_path / "test.xpr"
        project.touch()

        mock_install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )

        # Mock the subprocess
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(
            return_value=(b"Build completed successfully\n", b"")
        )

        with (
            patch(
                "vivado_mcp.vivado.build.get_default_vivado",
                return_value=mock_install,
            ),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ),
        ):
            result = await run_vivado_build(project)
            assert result.success is True
            assert result.vivado_version == "2023.2"
            assert len(result.errors) == 0

    @pytest.mark.asyncio
    async def test_build_with_errors(self, tmp_path: Path) -> None:
        """Test build that produces errors."""
        project = tmp_path / "test.xpr"
        project.touch()

        mock_install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )

        error_output = b"ERROR: [Synth 8-87] Signal 'clk' not found\n"
        mock_process = MagicMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(error_output, b""))

        with (
            patch(
                "vivado_mcp.vivado.build.get_default_vivado",
                return_value=mock_install,
            ),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ),
        ):
            result = await run_vivado_build(project)
            assert result.success is False
            assert result.exit_code == 1
            assert len(result.errors) == 1
            assert result.errors[0].id == "Synth 8-87"

    @pytest.mark.asyncio
    async def test_build_timeout(self, tmp_path: Path) -> None:
        """Test build timeout handling."""
        project = tmp_path / "test.xpr"
        project.touch()

        mock_install = VivadoInstallation(
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

        with (
            patch(
                "vivado_mcp.vivado.build.get_default_vivado",
                return_value=mock_install,
            ),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ),
        ):
            result = await run_vivado_build(project, timeout=1)
            assert result.success is False
            assert len(result.errors) == 1
            assert "timed out" in result.errors[0].message
            mock_process.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_custom_vivado_installation(self, tmp_path: Path) -> None:
        """Test using a custom Vivado installation."""
        project = tmp_path / "test.xpr"
        project.touch()

        custom_install = VivadoInstallation(
            version="2024.1",
            path=tmp_path / "Custom" / "Vivado" / "2024.1",
            executable=tmp_path / "Custom" / "Vivado" / "2024.1" / "bin" / "vivado",
        )

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"Success\n", b""))

        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_process,
        ) as mock_exec:
            result = await run_vivado_build(project, vivado_install=custom_install)
            assert result.success is True
            assert result.vivado_version == "2024.1"

            # Verify the custom executable was used
            call_args = mock_exec.call_args[0]
            assert "2024.1" in str(call_args[0])

    @pytest.mark.asyncio
    async def test_batch_mode_flags(self, tmp_path: Path) -> None:
        """Test that Vivado is called with correct batch mode flags."""
        project = tmp_path / "test.xpr"
        project.touch()

        mock_install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"Success\n", b""))

        with (
            patch(
                "vivado_mcp.vivado.build.get_default_vivado",
                return_value=mock_install,
            ),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ) as mock_exec,
        ):
            await run_vivado_build(project)

            # Check batch mode flags
            call_args = mock_exec.call_args[0]
            assert "-mode" in call_args
            mode_idx = call_args.index("-mode")
            assert call_args[mode_idx + 1] == "batch"

            # Check no GUI flags
            assert "-nojournal" in call_args
            assert "-nolog" in call_args


class TestRunStatus:
    """Tests for RunStatus dataclass."""

    def test_to_dict_basic(self) -> None:
        status = RunStatus(
            name="synth_1",
            state=BuildState.COMPLETED,
        )
        result = status.to_dict()
        assert result["name"] == "synth_1"
        assert result["state"] == "completed"
        assert result["progress"] is None
        assert result["status_message"] is None
        assert result["timestamp"] is None

    def test_to_dict_with_all_fields(self) -> None:
        status = RunStatus(
            name="impl_1",
            state=BuildState.IN_PROGRESS,
            progress="75%",
            status_message="place_design in progress",
            timestamp="2024-01-15T10:30:00",
        )
        result = status.to_dict()
        assert result["name"] == "impl_1"
        assert result["state"] == "in_progress"
        assert result["progress"] == "75%"
        assert result["status_message"] == "place_design in progress"
        assert result["timestamp"] == "2024-01-15T10:30:00"


class TestBuildStatus:
    """Tests for BuildStatus dataclass."""

    def test_to_dict_no_runs(self) -> None:
        status = BuildStatus(
            project_path="/path/to/project.xpr",
            overall_state=BuildState.NOT_STARTED,
            runs_directory_exists=False,
        )
        result = status.to_dict()
        assert result["project_path"] == "/path/to/project.xpr"
        assert result["overall_state"] == "not_started"
        assert result["synthesis"] is None
        assert result["implementation"] is None
        assert result["last_build_timestamp"] is None
        assert result["runs_directory_exists"] is False

    def test_to_dict_with_runs(self) -> None:
        synth = RunStatus(
            name="synth_1",
            state=BuildState.COMPLETED,
            progress="100%",
            status_message="synth_design Complete!",
        )
        impl = RunStatus(
            name="impl_1",
            state=BuildState.COMPLETED,
            progress="100%",
            status_message="write_bitstream Complete!",
        )
        status = BuildStatus(
            project_path="/path/to/project.xpr",
            overall_state=BuildState.COMPLETED,
            synthesis=synth,
            implementation=impl,
            last_build_timestamp="2024-01-15T12:00:00",
            runs_directory_exists=True,
        )
        result = status.to_dict()
        assert result["overall_state"] == "completed"
        assert result["synthesis"] is not None
        assert result["implementation"] is not None
        assert result["last_build_timestamp"] == "2024-01-15T12:00:00"


class TestGetRunDirectoryTimestamp:
    """Tests for _get_run_directory_timestamp function."""

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        result = _get_run_directory_timestamp(tmp_path / "nonexistent")
        assert result is None

    def test_empty_directory(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "synth_1"
        run_dir.mkdir()
        result = _get_run_directory_timestamp(run_dir)
        # Should return directory mtime
        assert result is not None

    def test_with_log_file(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "synth_1"
        run_dir.mkdir()
        log_file = run_dir / "runme.log"
        log_file.write_text("Build log content")
        result = _get_run_directory_timestamp(run_dir)
        assert result is not None


class TestParseRunStatus:
    """Tests for _parse_run_status function."""

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        result = _parse_run_status(tmp_path / "nonexistent", "synth_1")
        assert result.name == "synth_1"
        assert result.state == BuildState.NOT_STARTED

    def test_empty_directory(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "synth_1"
        run_dir.mkdir()
        result = _parse_run_status(run_dir, "synth_1")
        assert result.state == BuildState.NOT_STARTED

    def test_in_progress_with_begin_marker(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "synth_1"
        run_dir.mkdir()
        (run_dir / ".vivado.begin.rst").touch()
        result = _parse_run_status(run_dir, "synth_1")
        assert result.state == BuildState.IN_PROGRESS

    def test_completed_with_end_marker(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "synth_1"
        run_dir.mkdir()
        (run_dir / ".vivado.begin.rst").touch()
        (run_dir / ".vivado.end.rst").touch()
        result = _parse_run_status(run_dir, "synth_1")
        assert result.state == BuildState.COMPLETED

    def test_failed_with_error_marker(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "synth_1"
        run_dir.mkdir()
        (run_dir / ".vivado.begin.rst").touch()
        (run_dir / ".vivado.error.rst").touch()
        result = _parse_run_status(run_dir, "synth_1")
        assert result.state == BuildState.FAILED

    def test_completed_with_log_success(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "synth_1"
        run_dir.mkdir()
        (run_dir / ".vivado.begin.rst").touch()
        (run_dir / ".vivado.end.rst").touch()
        log_file = run_dir / "runme.log"
        log_file.write_text("Some output\nsynth_design Complete!\nMore output")
        result = _parse_run_status(run_dir, "synth_1")
        assert result.state == BuildState.COMPLETED
        assert result.status_message == "synth_design Complete!"

    def test_failed_with_error_in_log(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "synth_1"
        run_dir.mkdir()
        log_file = run_dir / "runme.log"
        log_file.write_text("ERROR: [Synth 8-87] Signal not found")
        result = _parse_run_status(run_dir, "synth_1")
        assert result.state == BuildState.FAILED
        assert result.status_message == "Build failed with errors"

    def test_progress_parsing(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "impl_1"
        run_dir.mkdir()
        (run_dir / ".vivado.begin.rst").touch()
        log_file = run_dir / "runme.log"
        log_file.write_text("Progress: 25%\nProgress: 50%\nProgress: 75%\n")
        result = _parse_run_status(run_dir, "impl_1")
        assert result.state == BuildState.IN_PROGRESS
        assert result.progress == "75%"

    def test_impl_completed_with_bitstream(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "impl_1"
        run_dir.mkdir()
        (run_dir / ".vivado.begin.rst").touch()
        (run_dir / ".vivado.end.rst").touch()
        (run_dir / "design.bit").touch()  # Bitstream file
        result = _parse_run_status(run_dir, "impl_1")
        assert result.state == BuildState.COMPLETED
        assert result.status_message == "Bitstream generated"

    def test_incomplete_run_with_log_only(self, tmp_path: Path) -> None:
        run_dir = tmp_path / "synth_1"
        run_dir.mkdir()
        log_file = run_dir / "runme.log"
        log_file.write_text("Starting synthesis...")
        result = _parse_run_status(run_dir, "synth_1")
        assert result.state == BuildState.FAILED
        assert result.status_message == "Run incomplete or interrupted"


class TestGetBuildStatus:
    """Tests for get_build_status function."""

    def test_no_runs_directory(self, tmp_path: Path) -> None:
        project = tmp_path / "project.xpr"
        project.touch()
        result = get_build_status(project)
        assert result.overall_state == BuildState.NOT_STARTED
        assert result.runs_directory_exists is False
        assert result.synthesis is None
        assert result.implementation is None

    def test_with_directory_path(self, tmp_path: Path) -> None:
        # Test passing a directory instead of a file
        result = get_build_status(tmp_path)
        assert result.overall_state == BuildState.NOT_STARTED
        assert result.runs_directory_exists is False

    def test_runs_directory_exists_no_runs(self, tmp_path: Path) -> None:
        project = tmp_path / "test.xpr"
        project.touch()
        runs_dir = tmp_path / "test.runs"
        runs_dir.mkdir()
        result = get_build_status(project)
        assert result.runs_directory_exists is True
        assert result.synthesis is not None
        assert result.synthesis.state == BuildState.NOT_STARTED

    def test_synthesis_in_progress(self, tmp_path: Path) -> None:
        project = tmp_path / "test.xpr"
        project.touch()
        runs_dir = tmp_path / "test.runs"
        runs_dir.mkdir()
        synth_dir = runs_dir / "synth_1"
        synth_dir.mkdir()
        (synth_dir / ".vivado.begin.rst").touch()

        result = get_build_status(project)
        assert result.overall_state == BuildState.IN_PROGRESS
        assert result.synthesis is not None
        assert result.synthesis.state == BuildState.IN_PROGRESS

    def test_synthesis_completed_impl_not_started(self, tmp_path: Path) -> None:
        project = tmp_path / "test.xpr"
        project.touch()
        runs_dir = tmp_path / "test.runs"
        runs_dir.mkdir()
        synth_dir = runs_dir / "synth_1"
        synth_dir.mkdir()
        (synth_dir / ".vivado.begin.rst").touch()
        (synth_dir / ".vivado.end.rst").touch()

        result = get_build_status(project)
        assert result.overall_state == BuildState.COMPLETED
        assert result.synthesis is not None
        assert result.synthesis.state == BuildState.COMPLETED
        assert result.implementation is not None
        assert result.implementation.state == BuildState.NOT_STARTED

    def test_full_build_completed(self, tmp_path: Path) -> None:
        project = tmp_path / "test.xpr"
        project.touch()
        runs_dir = tmp_path / "test.runs"
        runs_dir.mkdir()

        # Synthesis complete
        synth_dir = runs_dir / "synth_1"
        synth_dir.mkdir()
        (synth_dir / ".vivado.begin.rst").touch()
        (synth_dir / ".vivado.end.rst").touch()
        (synth_dir / "runme.log").write_text("synth_design Complete!")

        # Implementation complete
        impl_dir = runs_dir / "impl_1"
        impl_dir.mkdir()
        (impl_dir / ".vivado.begin.rst").touch()
        (impl_dir / ".vivado.end.rst").touch()
        (impl_dir / "design.bit").touch()

        result = get_build_status(project)
        assert result.overall_state == BuildState.COMPLETED
        assert result.synthesis is not None
        assert result.synthesis.state == BuildState.COMPLETED
        assert result.implementation is not None
        assert result.implementation.state == BuildState.COMPLETED

    def test_synthesis_failed(self, tmp_path: Path) -> None:
        project = tmp_path / "test.xpr"
        project.touch()
        runs_dir = tmp_path / "test.runs"
        runs_dir.mkdir()

        synth_dir = runs_dir / "synth_1"
        synth_dir.mkdir()
        (synth_dir / ".vivado.begin.rst").touch()
        (synth_dir / ".vivado.error.rst").touch()

        result = get_build_status(project)
        assert result.overall_state == BuildState.FAILED
        assert result.synthesis is not None
        assert result.synthesis.state == BuildState.FAILED

    def test_implementation_failed(self, tmp_path: Path) -> None:
        project = tmp_path / "test.xpr"
        project.touch()
        runs_dir = tmp_path / "test.runs"
        runs_dir.mkdir()

        # Synthesis complete
        synth_dir = runs_dir / "synth_1"
        synth_dir.mkdir()
        (synth_dir / ".vivado.begin.rst").touch()
        (synth_dir / ".vivado.end.rst").touch()

        # Implementation failed
        impl_dir = runs_dir / "impl_1"
        impl_dir.mkdir()
        (impl_dir / "runme.log").write_text("ERROR: [Place 30-876] Placement failed")

        result = get_build_status(project)
        assert result.overall_state == BuildState.FAILED
        assert result.implementation is not None
        assert result.implementation.state == BuildState.FAILED

    def test_finds_runs_dir_by_glob(self, tmp_path: Path) -> None:
        # Test finding .runs directory when project name differs
        runs_dir = tmp_path / "different_name.runs"
        runs_dir.mkdir()
        synth_dir = runs_dir / "synth_1"
        synth_dir.mkdir()
        (synth_dir / ".vivado.begin.rst").touch()
        (synth_dir / ".vivado.end.rst").touch()

        result = get_build_status(tmp_path)
        assert result.runs_directory_exists is True
        assert result.synthesis is not None
        assert result.synthesis.state == BuildState.COMPLETED

    def test_timestamp_returned(self, tmp_path: Path) -> None:
        project = tmp_path / "test.xpr"
        project.touch()
        runs_dir = tmp_path / "test.runs"
        runs_dir.mkdir()
        synth_dir = runs_dir / "synth_1"
        synth_dir.mkdir()
        (synth_dir / ".vivado.begin.rst").touch()
        (synth_dir / ".vivado.end.rst").touch()
        (synth_dir / "runme.log").write_text("Build log")

        result = get_build_status(project)
        assert result.last_build_timestamp is not None


class TestGenerateSynthesisTcl:
    """Tests for TCL script generation for synthesis-only."""

    def test_xpr_project(self, tmp_path: Path) -> None:
        project = tmp_path / "test.xpr"
        tcl = _generate_synthesis_tcl(project)

        # Verify synthesis-only commands are present
        assert "open_project" in tcl
        assert "synth_1" in tcl
        assert "Synthesis completed successfully" in tcl

        # Verify implementation commands are NOT present
        assert "impl_1" not in tcl
        assert "write_bitstream" not in tcl
        assert "opt_design" not in tcl
        assert "place_design" not in tcl
        assert "route_design" not in tcl

    def test_tcl_project(self, tmp_path: Path) -> None:
        project = tmp_path / "build.tcl"
        tcl = _generate_synthesis_tcl(project)

        # For TCL projects, should source the file
        assert "source" in tcl
        assert "synth_design" in tcl
        assert "Synthesis completed successfully" in tcl

        # Verify implementation commands are NOT present
        assert "opt_design" not in tcl
        assert "place_design" not in tcl
        assert "route_design" not in tcl
        assert "write_bitstream" not in tcl


class TestRunSynthesis:
    """Tests for the synthesis-only function."""

    @pytest.mark.asyncio
    async def test_project_not_found(self, tmp_path: Path) -> None:
        """Test handling of non-existent project file."""
        result = await run_synthesis(tmp_path / "nonexistent.xpr")
        assert result.success is False
        assert len(result.errors) == 1
        assert "not found" in result.errors[0].message

    @pytest.mark.asyncio
    async def test_invalid_project_type(self, tmp_path: Path) -> None:
        """Test handling of invalid file type."""
        invalid_file = tmp_path / "design.v"
        invalid_file.touch()
        result = await run_synthesis(invalid_file)
        assert result.success is False
        assert len(result.errors) == 1
        assert "Invalid project file type" in result.errors[0].message

    @pytest.mark.asyncio
    async def test_no_vivado_installation(self, tmp_path: Path) -> None:
        """Test handling when no Vivado is found."""
        project = tmp_path / "test.xpr"
        project.touch()

        with patch(
            "vivado_mcp.vivado.build.get_default_vivado",
            return_value=None,
        ):
            result = await run_synthesis(project)
            assert result.success is False
            assert len(result.errors) == 1
            assert "No Vivado installation found" in result.errors[0].message

    @pytest.mark.asyncio
    async def test_successful_synthesis(self, tmp_path: Path) -> None:
        """Test successful synthesis execution."""
        project = tmp_path / "test.xpr"
        project.touch()

        mock_install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )

        # Mock the subprocess
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(
            return_value=(b"Synthesis completed successfully\n", b"")
        )

        with (
            patch(
                "vivado_mcp.vivado.build.get_default_vivado",
                return_value=mock_install,
            ),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ),
        ):
            result = await run_synthesis(project)
            assert result.success is True
            assert result.vivado_version == "2023.2"
            assert len(result.errors) == 0

    @pytest.mark.asyncio
    async def test_synthesis_with_errors(self, tmp_path: Path) -> None:
        """Test synthesis that produces errors."""
        project = tmp_path / "test.xpr"
        project.touch()

        mock_install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )

        error_output = b"ERROR: [Synth 8-87] Signal 'clk' not found\n"
        mock_process = MagicMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(error_output, b""))

        with (
            patch(
                "vivado_mcp.vivado.build.get_default_vivado",
                return_value=mock_install,
            ),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ),
        ):
            result = await run_synthesis(project)
            assert result.success is False
            assert result.exit_code == 1
            assert len(result.errors) == 1
            assert result.errors[0].id == "Synth 8-87"

    @pytest.mark.asyncio
    async def test_synthesis_timeout(self, tmp_path: Path) -> None:
        """Test synthesis timeout handling."""
        project = tmp_path / "test.xpr"
        project.touch()

        mock_install = VivadoInstallation(
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

        with (
            patch(
                "vivado_mcp.vivado.build.get_default_vivado",
                return_value=mock_install,
            ),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ),
        ):
            result = await run_synthesis(project, timeout=1)
            assert result.success is False
            assert len(result.errors) == 1
            assert "timed out" in result.errors[0].message
            mock_process.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_custom_vivado_installation(self, tmp_path: Path) -> None:
        """Test using a custom Vivado installation."""
        project = tmp_path / "test.xpr"
        project.touch()

        custom_install = VivadoInstallation(
            version="2024.1",
            path=tmp_path / "Custom" / "Vivado" / "2024.1",
            executable=tmp_path / "Custom" / "Vivado" / "2024.1" / "bin" / "vivado",
        )

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"Success\n", b""))

        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_process,
        ) as mock_exec:
            result = await run_synthesis(project, vivado_install=custom_install)
            assert result.success is True
            assert result.vivado_version == "2024.1"

            # Verify the custom executable was used
            call_args = mock_exec.call_args[0]
            assert "2024.1" in str(call_args[0])

    @pytest.mark.asyncio
    async def test_batch_mode_flags(self, tmp_path: Path) -> None:
        """Test that Vivado is called with correct batch mode flags."""
        project = tmp_path / "test.xpr"
        project.touch()

        mock_install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"Success\n", b""))

        with (
            patch(
                "vivado_mcp.vivado.build.get_default_vivado",
                return_value=mock_install,
            ),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ) as mock_exec,
        ):
            await run_synthesis(project)

            # Check batch mode flags
            call_args = mock_exec.call_args[0]
            assert "-mode" in call_args
            mode_idx = call_args.index("-mode")
            assert call_args[mode_idx + 1] == "batch"

            # Check no GUI flags
            assert "-nojournal" in call_args
            assert "-nolog" in call_args

    @pytest.mark.asyncio
    async def test_synthesis_with_critical_warnings(self, tmp_path: Path) -> None:
        """Test synthesis that produces critical warnings."""
        project = tmp_path / "test.xpr"
        project.touch()

        mock_install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )

        warning_output = (
            b"CRITICAL WARNING: [Synth 8-5546] Missing constraint file\n"
            b"Synthesis completed successfully\n"
        )
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(warning_output, b""))

        with (
            patch(
                "vivado_mcp.vivado.build.get_default_vivado",
                return_value=mock_install,
            ),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ),
        ):
            result = await run_synthesis(project)
            assert result.success is True
            assert len(result.errors) == 0
            assert len(result.critical_warnings) == 1
            assert result.critical_warnings[0].id == "Synth 8-5546"


class TestGenerateImplementationTcl:
    """Tests for TCL script generation for implementation-only."""

    def test_xpr_project(self, tmp_path: Path) -> None:
        project = tmp_path / "test.xpr"
        tcl = _generate_implementation_tcl(project)

        # Verify implementation-only commands are present
        assert "open_project" in tcl
        assert "impl_1" in tcl
        assert "Implementation completed successfully" in tcl
        assert "write_bitstream" in tcl

        # Verify synthesis check is present (to require completed synthesis)
        assert "Synthesis not complete" in tcl
        assert "synth_1" in tcl

        # Verify synthesis execution commands are NOT present
        assert "reset_run synth_1" not in tcl
        assert "launch_runs synth_1" not in tcl

    def test_tcl_project(self, tmp_path: Path) -> None:
        project = tmp_path / "build.tcl"
        tcl = _generate_implementation_tcl(project)

        # For TCL projects, should source the file
        assert "source" in tcl
        assert "opt_design" in tcl
        assert "place_design" in tcl
        assert "route_design" in tcl
        assert "write_bitstream" in tcl
        assert "Implementation completed successfully" in tcl

        # Verify synthesis commands are NOT present
        assert "synth_design" not in tcl


class TestRunImplementation:
    """Tests for the implementation-only function."""

    @pytest.mark.asyncio
    async def test_project_not_found(self, tmp_path: Path) -> None:
        """Test handling of non-existent project file."""
        result = await run_implementation(tmp_path / "nonexistent.xpr")
        assert result.success is False
        assert len(result.errors) == 1
        assert "not found" in result.errors[0].message

    @pytest.mark.asyncio
    async def test_invalid_project_type(self, tmp_path: Path) -> None:
        """Test handling of invalid file type."""
        invalid_file = tmp_path / "design.v"
        invalid_file.touch()
        result = await run_implementation(invalid_file)
        assert result.success is False
        assert len(result.errors) == 1
        assert "Invalid project file type" in result.errors[0].message

    @pytest.mark.asyncio
    async def test_synthesis_not_complete(self, tmp_path: Path) -> None:
        """Test handling when synthesis is not complete."""
        project = tmp_path / "test.xpr"
        project.touch()

        # No runs directory exists, so synthesis is not complete
        result = await run_implementation(project)
        assert result.success is False
        assert len(result.errors) == 1
        assert "Synthesis not complete" in result.errors[0].message

    @pytest.mark.asyncio
    async def test_no_vivado_installation(self, tmp_path: Path) -> None:
        """Test handling when no Vivado is found."""
        project = tmp_path / "test.xpr"
        project.touch()

        # Create completed synthesis run
        runs_dir = tmp_path / "test.runs"
        runs_dir.mkdir()
        synth_dir = runs_dir / "synth_1"
        synth_dir.mkdir()
        (synth_dir / ".vivado.begin.rst").touch()
        (synth_dir / ".vivado.end.rst").touch()
        (synth_dir / "runme.log").write_text("synth_design Complete!")

        with patch(
            "vivado_mcp.vivado.build.get_default_vivado",
            return_value=None,
        ):
            result = await run_implementation(project)
            assert result.success is False
            assert len(result.errors) == 1
            assert "No Vivado installation found" in result.errors[0].message

    @pytest.mark.asyncio
    async def test_successful_implementation(self, tmp_path: Path) -> None:
        """Test successful implementation execution."""
        project = tmp_path / "test.xpr"
        project.touch()

        # Create completed synthesis run
        runs_dir = tmp_path / "test.runs"
        runs_dir.mkdir()
        synth_dir = runs_dir / "synth_1"
        synth_dir.mkdir()
        (synth_dir / ".vivado.begin.rst").touch()
        (synth_dir / ".vivado.end.rst").touch()
        (synth_dir / "runme.log").write_text("synth_design Complete!")

        mock_install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )

        # Mock the subprocess
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(
            return_value=(b"Implementation completed successfully\n", b"")
        )

        with (
            patch(
                "vivado_mcp.vivado.build.get_default_vivado",
                return_value=mock_install,
            ),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ),
        ):
            result = await run_implementation(project)
            assert result.success is True
            assert result.vivado_version == "2023.2"
            assert len(result.errors) == 0

    @pytest.mark.asyncio
    async def test_implementation_with_errors(self, tmp_path: Path) -> None:
        """Test implementation that produces errors."""
        project = tmp_path / "test.xpr"
        project.touch()

        # Create completed synthesis run
        runs_dir = tmp_path / "test.runs"
        runs_dir.mkdir()
        synth_dir = runs_dir / "synth_1"
        synth_dir.mkdir()
        (synth_dir / ".vivado.begin.rst").touch()
        (synth_dir / ".vivado.end.rst").touch()
        (synth_dir / "runme.log").write_text("synth_design Complete!")

        mock_install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )

        error_output = b"ERROR: [Place 30-876] Placement failed for cell\n"
        mock_process = MagicMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(error_output, b""))

        with (
            patch(
                "vivado_mcp.vivado.build.get_default_vivado",
                return_value=mock_install,
            ),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ),
        ):
            result = await run_implementation(project)
            assert result.success is False
            assert result.exit_code == 1
            assert len(result.errors) == 1
            assert result.errors[0].id == "Place 30-876"

    @pytest.mark.asyncio
    async def test_implementation_timeout(self, tmp_path: Path) -> None:
        """Test implementation timeout handling."""
        project = tmp_path / "test.xpr"
        project.touch()

        # Create completed synthesis run
        runs_dir = tmp_path / "test.runs"
        runs_dir.mkdir()
        synth_dir = runs_dir / "synth_1"
        synth_dir.mkdir()
        (synth_dir / ".vivado.begin.rst").touch()
        (synth_dir / ".vivado.end.rst").touch()
        (synth_dir / "runme.log").write_text("synth_design Complete!")

        mock_install = VivadoInstallation(
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

        with (
            patch(
                "vivado_mcp.vivado.build.get_default_vivado",
                return_value=mock_install,
            ),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ),
        ):
            result = await run_implementation(project, timeout=1)
            assert result.success is False
            assert len(result.errors) == 1
            assert "timed out" in result.errors[0].message
            mock_process.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_custom_vivado_installation(self, tmp_path: Path) -> None:
        """Test using a custom Vivado installation."""
        project = tmp_path / "test.xpr"
        project.touch()

        # Create completed synthesis run
        runs_dir = tmp_path / "test.runs"
        runs_dir.mkdir()
        synth_dir = runs_dir / "synth_1"
        synth_dir.mkdir()
        (synth_dir / ".vivado.begin.rst").touch()
        (synth_dir / ".vivado.end.rst").touch()
        (synth_dir / "runme.log").write_text("synth_design Complete!")

        custom_install = VivadoInstallation(
            version="2024.1",
            path=tmp_path / "Custom" / "Vivado" / "2024.1",
            executable=tmp_path / "Custom" / "Vivado" / "2024.1" / "bin" / "vivado",
        )

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"Success\n", b""))

        with patch(
            "asyncio.create_subprocess_exec",
            return_value=mock_process,
        ) as mock_exec:
            result = await run_implementation(project, vivado_install=custom_install)
            assert result.success is True
            assert result.vivado_version == "2024.1"

            # Verify the custom executable was used
            call_args = mock_exec.call_args[0]
            assert "2024.1" in str(call_args[0])

    @pytest.mark.asyncio
    async def test_batch_mode_flags(self, tmp_path: Path) -> None:
        """Test that Vivado is called with correct batch mode flags."""
        project = tmp_path / "test.xpr"
        project.touch()

        # Create completed synthesis run
        runs_dir = tmp_path / "test.runs"
        runs_dir.mkdir()
        synth_dir = runs_dir / "synth_1"
        synth_dir.mkdir()
        (synth_dir / ".vivado.begin.rst").touch()
        (synth_dir / ".vivado.end.rst").touch()
        (synth_dir / "runme.log").write_text("synth_design Complete!")

        mock_install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(b"Success\n", b""))

        with (
            patch(
                "vivado_mcp.vivado.build.get_default_vivado",
                return_value=mock_install,
            ),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ) as mock_exec,
        ):
            await run_implementation(project)

            # Check batch mode flags
            call_args = mock_exec.call_args[0]
            assert "-mode" in call_args
            mode_idx = call_args.index("-mode")
            assert call_args[mode_idx + 1] == "batch"

            # Check no GUI flags
            assert "-nojournal" in call_args
            assert "-nolog" in call_args

    @pytest.mark.asyncio
    async def test_implementation_with_critical_warnings(self, tmp_path: Path) -> None:
        """Test implementation that produces critical warnings."""
        project = tmp_path / "test.xpr"
        project.touch()

        # Create completed synthesis run
        runs_dir = tmp_path / "test.runs"
        runs_dir.mkdir()
        synth_dir = runs_dir / "synth_1"
        synth_dir.mkdir()
        (synth_dir / ".vivado.begin.rst").touch()
        (synth_dir / ".vivado.end.rst").touch()
        (synth_dir / "runme.log").write_text("synth_design Complete!")

        mock_install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )

        warning_output = (
            b"CRITICAL WARNING: [Route 35-39] Timing constraints not met\n"
            b"Implementation completed successfully\n"
        )
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(warning_output, b""))

        with (
            patch(
                "vivado_mcp.vivado.build.get_default_vivado",
                return_value=mock_install,
            ),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ),
        ):
            result = await run_implementation(project)
            assert result.success is True
            assert len(result.errors) == 0
            assert len(result.critical_warnings) == 1
            assert result.critical_warnings[0].id == "Route 35-39"

    @pytest.mark.asyncio
    async def test_tcl_project_skips_synthesis_check(self, tmp_path: Path) -> None:
        """Test that TCL projects skip the synthesis completion check."""
        project = tmp_path / "build.tcl"
        project.touch()

        # No runs directory - for TCL projects, this is OK
        mock_install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )

        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(
            return_value=(b"Implementation completed successfully\n", b"")
        )

        with (
            patch(
                "vivado_mcp.vivado.build.get_default_vivado",
                return_value=mock_install,
            ),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ),
        ):
            result = await run_implementation(project)
            # TCL projects don't require synthesis check
            assert result.success is True


class TestBitstreamResult:
    """Tests for BitstreamResult dataclass."""

    def test_to_dict_success(self) -> None:
        result = BitstreamResult(
            success=True,
            project_path="/path/to/project.xpr",
            vivado_version="2023.2",
            bitstream_path="/path/to/output.bit",
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["project_path"] == "/path/to/project.xpr"
        assert d["vivado_version"] == "2023.2"
        assert d["bitstream_path"] == "/path/to/output.bit"
        assert d["errors"] == []
        assert d["critical_warnings"] == []
        assert d["error_count"] == 0
        assert d["critical_warning_count"] == 0
        assert d["exit_code"] == 0

    def test_to_dict_with_errors(self) -> None:
        error = BuildMessage(
            severity="ERROR",
            id="Bitstream 12-34",
            message="Bitstream generation failed",
        )
        result = BitstreamResult(
            success=False,
            project_path="/path/to/project.xpr",
            vivado_version="2023.2",
            bitstream_path=None,
            errors=[error],
            exit_code=1,
        )
        d = result.to_dict()
        assert d["success"] is False
        assert d["bitstream_path"] is None
        assert d["error_count"] == 1
        assert len(d["errors"]) == 1  # type: ignore[arg-type]


class TestParseBitstreamPath:
    """Tests for _parse_bitstream_path function."""

    def test_parse_bitstream_path_found(self) -> None:
        output = "Some output\nBITSTREAM_FILE: /path/to/design.bit\nMore output"
        result = _parse_bitstream_path(output)
        assert result == "/path/to/design.bit"

    def test_parse_bitstream_path_not_found(self) -> None:
        output = "Some output without bitstream file path"
        result = _parse_bitstream_path(output)
        assert result is None

    def test_parse_bitstream_path_with_spaces(self) -> None:
        output = "BITSTREAM_FILE:   /path/with spaces/design.bit  \n"
        result = _parse_bitstream_path(output)
        assert result == "/path/with spaces/design.bit"


class TestFindBitstreamFile:
    """Tests for _find_bitstream_file function."""

    def test_find_bitstream_file_found(self, tmp_path: Path) -> None:
        project = tmp_path / "test.xpr"
        project.touch()
        runs_dir = tmp_path / "test.runs"
        runs_dir.mkdir()
        impl_dir = runs_dir / "impl_1"
        impl_dir.mkdir()
        bitstream = impl_dir / "design.bit"
        bitstream.touch()

        result = _find_bitstream_file(project)
        assert result == str(bitstream)

    def test_find_bitstream_file_not_found(self, tmp_path: Path) -> None:
        project = tmp_path / "test.xpr"
        project.touch()

        result = _find_bitstream_file(project)
        assert result is None

    def test_find_bitstream_file_impl_dir_exists_no_bit(self, tmp_path: Path) -> None:
        project = tmp_path / "test.xpr"
        project.touch()
        runs_dir = tmp_path / "test.runs"
        runs_dir.mkdir()
        impl_dir = runs_dir / "impl_1"
        impl_dir.mkdir()

        result = _find_bitstream_file(project)
        assert result is None


class TestGenerateBitstreamTcl:
    """Tests for TCL script generation for bitstream-only."""

    def test_xpr_project(self, tmp_path: Path) -> None:
        project = tmp_path / "test.xpr"
        tcl = _generate_bitstream_tcl(project)

        # Verify bitstream-only commands are present
        assert "open_project" in tcl
        assert "impl_1" in tcl
        assert "write_bitstream" in tcl
        assert "Bitstream generation completed successfully" in tcl
        assert "BITSTREAM_FILE:" in tcl

        # Verify implementation check is present (to require completed implementation)
        assert "Implementation not complete" in tcl

        # Verify synthesis check is present
        assert "Synthesis not complete" in tcl

        # Verify implementation execution commands are NOT present
        assert "reset_run impl_1" not in tcl
        assert "opt_design" not in tcl
        assert "place_design" not in tcl
        assert "route_design" not in tcl

    def test_tcl_project(self, tmp_path: Path) -> None:
        project = tmp_path / "build.tcl"
        tcl = _generate_bitstream_tcl(project)

        # For TCL projects, should source the file
        assert "source" in tcl
        assert "write_bitstream" in tcl
        assert "Bitstream generation completed successfully" in tcl
        assert "BITSTREAM_FILE:" in tcl

        # Verify implementation commands are NOT present
        assert "opt_design" not in tcl
        assert "place_design" not in tcl
        assert "route_design" not in tcl


class TestRunBitstreamGeneration:
    """Tests for the bitstream generation function."""

    @pytest.mark.asyncio
    async def test_project_not_found(self, tmp_path: Path) -> None:
        """Test handling of non-existent project file."""
        result = await run_bitstream_generation(tmp_path / "nonexistent.xpr")
        assert result.success is False
        assert len(result.errors) == 1
        assert "not found" in result.errors[0].message

    @pytest.mark.asyncio
    async def test_invalid_project_type(self, tmp_path: Path) -> None:
        """Test handling of invalid file type."""
        invalid_file = tmp_path / "design.v"
        invalid_file.touch()
        result = await run_bitstream_generation(invalid_file)
        assert result.success is False
        assert len(result.errors) == 1
        assert "Invalid project file type" in result.errors[0].message

    @pytest.mark.asyncio
    async def test_implementation_not_complete(self, tmp_path: Path) -> None:
        """Test handling when implementation is not complete."""
        project = tmp_path / "test.xpr"
        project.touch()

        # Create synthesis complete but implementation not complete
        runs_dir = tmp_path / "test.runs"
        runs_dir.mkdir()
        synth_dir = runs_dir / "synth_1"
        synth_dir.mkdir()
        (synth_dir / ".vivado.begin.rst").touch()
        (synth_dir / ".vivado.end.rst").touch()
        (synth_dir / "runme.log").write_text("synth_design Complete!")

        # impl_1 not complete
        result = await run_bitstream_generation(project)
        assert result.success is False
        assert len(result.errors) == 1
        assert "Implementation not complete" in result.errors[0].message

    @pytest.mark.asyncio
    async def test_no_vivado_installation(self, tmp_path: Path) -> None:
        """Test handling when no Vivado is found."""
        project = tmp_path / "test.xpr"
        project.touch()

        # Create completed implementation run
        runs_dir = tmp_path / "test.runs"
        runs_dir.mkdir()
        synth_dir = runs_dir / "synth_1"
        synth_dir.mkdir()
        (synth_dir / ".vivado.begin.rst").touch()
        (synth_dir / ".vivado.end.rst").touch()
        (synth_dir / "runme.log").write_text("synth_design Complete!")
        impl_dir = runs_dir / "impl_1"
        impl_dir.mkdir()
        (impl_dir / ".vivado.begin.rst").touch()
        (impl_dir / ".vivado.end.rst").touch()
        (impl_dir / "design.bit").touch()

        with patch(
            "vivado_mcp.vivado.build.get_default_vivado",
            return_value=None,
        ):
            result = await run_bitstream_generation(project)
            assert result.success is False
            assert len(result.errors) == 1
            assert "No Vivado installation found" in result.errors[0].message

    @pytest.mark.asyncio
    async def test_successful_bitstream_generation(self, tmp_path: Path) -> None:
        """Test successful bitstream generation."""
        project = tmp_path / "test.xpr"
        project.touch()

        # Create completed implementation run
        runs_dir = tmp_path / "test.runs"
        runs_dir.mkdir()
        synth_dir = runs_dir / "synth_1"
        synth_dir.mkdir()
        (synth_dir / ".vivado.begin.rst").touch()
        (synth_dir / ".vivado.end.rst").touch()
        (synth_dir / "runme.log").write_text("synth_design Complete!")
        impl_dir = runs_dir / "impl_1"
        impl_dir.mkdir()
        (impl_dir / ".vivado.begin.rst").touch()
        (impl_dir / ".vivado.end.rst").touch()
        (impl_dir / "design.bit").touch()

        mock_install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )

        bitstream_path = str(impl_dir / "design.bit")
        mock_process = MagicMock()
        mock_process.returncode = 0
        output = (
            f"BITSTREAM_FILE: {bitstream_path}\n"
            f"Bitstream generation completed successfully\n"
        ).encode()
        mock_process.communicate = AsyncMock(return_value=(output, b""))

        with (
            patch(
                "vivado_mcp.vivado.build.get_default_vivado",
                return_value=mock_install,
            ),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ),
        ):
            result = await run_bitstream_generation(project)
            assert result.success is True
            assert result.vivado_version == "2023.2"
            assert result.bitstream_path == bitstream_path
            assert len(result.errors) == 0

    @pytest.mark.asyncio
    async def test_bitstream_generation_with_errors(self, tmp_path: Path) -> None:
        """Test bitstream generation that produces errors."""
        project = tmp_path / "test.xpr"
        project.touch()

        # Create completed implementation run
        runs_dir = tmp_path / "test.runs"
        runs_dir.mkdir()
        synth_dir = runs_dir / "synth_1"
        synth_dir.mkdir()
        (synth_dir / ".vivado.begin.rst").touch()
        (synth_dir / ".vivado.end.rst").touch()
        (synth_dir / "runme.log").write_text("synth_design Complete!")
        impl_dir = runs_dir / "impl_1"
        impl_dir.mkdir()
        (impl_dir / ".vivado.begin.rst").touch()
        (impl_dir / ".vivado.end.rst").touch()
        (impl_dir / "design.bit").touch()

        mock_install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )

        error_output = b"ERROR: [Bitstream 12-34] DRC violation\n"
        mock_process = MagicMock()
        mock_process.returncode = 1
        mock_process.communicate = AsyncMock(return_value=(error_output, b""))

        with (
            patch(
                "vivado_mcp.vivado.build.get_default_vivado",
                return_value=mock_install,
            ),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ),
        ):
            result = await run_bitstream_generation(project)
            assert result.success is False
            assert result.exit_code == 1
            assert len(result.errors) == 1
            assert result.errors[0].id == "Bitstream 12-34"

    @pytest.mark.asyncio
    async def test_bitstream_generation_timeout(self, tmp_path: Path) -> None:
        """Test bitstream generation timeout handling."""
        project = tmp_path / "test.xpr"
        project.touch()

        # Create completed implementation run
        runs_dir = tmp_path / "test.runs"
        runs_dir.mkdir()
        synth_dir = runs_dir / "synth_1"
        synth_dir.mkdir()
        (synth_dir / ".vivado.begin.rst").touch()
        (synth_dir / ".vivado.end.rst").touch()
        (synth_dir / "runme.log").write_text("synth_design Complete!")
        impl_dir = runs_dir / "impl_1"
        impl_dir.mkdir()
        (impl_dir / ".vivado.begin.rst").touch()
        (impl_dir / ".vivado.end.rst").touch()
        (impl_dir / "design.bit").touch()

        mock_install = VivadoInstallation(
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

        with (
            patch(
                "vivado_mcp.vivado.build.get_default_vivado",
                return_value=mock_install,
            ),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ),
        ):
            result = await run_bitstream_generation(project, timeout=1)
            assert result.success is False
            assert len(result.errors) == 1
            assert "timed out" in result.errors[0].message
            mock_process.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_bitstream_generation_with_critical_warnings(self, tmp_path: Path) -> None:
        """Test bitstream generation that produces critical warnings."""
        project = tmp_path / "test.xpr"
        project.touch()

        # Create completed implementation run
        runs_dir = tmp_path / "test.runs"
        runs_dir.mkdir()
        synth_dir = runs_dir / "synth_1"
        synth_dir.mkdir()
        (synth_dir / ".vivado.begin.rst").touch()
        (synth_dir / ".vivado.end.rst").touch()
        (synth_dir / "runme.log").write_text("synth_design Complete!")
        impl_dir = runs_dir / "impl_1"
        impl_dir.mkdir()
        (impl_dir / ".vivado.begin.rst").touch()
        (impl_dir / ".vivado.end.rst").touch()
        (impl_dir / "design.bit").touch()

        mock_install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )

        bitstream_path = str(impl_dir / "design.bit")
        warning_output = (
            f"CRITICAL WARNING: [DRC RPBF-3] Some DRC warning\n"
            f"BITSTREAM_FILE: {bitstream_path}\n"
            f"Bitstream generation completed successfully\n"
        ).encode()
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(return_value=(warning_output, b""))

        with (
            patch(
                "vivado_mcp.vivado.build.get_default_vivado",
                return_value=mock_install,
            ),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ),
        ):
            result = await run_bitstream_generation(project)
            assert result.success is True
            assert result.bitstream_path == bitstream_path
            assert len(result.errors) == 0
            assert len(result.critical_warnings) == 1
            assert result.critical_warnings[0].id == "DRC RPBF-3"

    @pytest.mark.asyncio
    async def test_tcl_project_skips_implementation_check(self, tmp_path: Path) -> None:
        """Test that TCL projects skip the implementation completion check."""
        project = tmp_path / "build.tcl"
        project.touch()

        # No runs directory - for TCL projects, this is OK
        mock_install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )

        mock_process = MagicMock()
        mock_process.returncode = 0
        output = (
            b"BITSTREAM_FILE: /path/to/output.bit\n"
            b"Bitstream generation completed successfully\n"
        )
        mock_process.communicate = AsyncMock(return_value=(output, b""))

        with (
            patch(
                "vivado_mcp.vivado.build.get_default_vivado",
                return_value=mock_install,
            ),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ),
        ):
            result = await run_bitstream_generation(project)
            # TCL projects don't require implementation check
            assert result.success is True
            assert result.bitstream_path == "/path/to/output.bit"

    @pytest.mark.asyncio
    async def test_bitstream_path_fallback_to_file_search(self, tmp_path: Path) -> None:
        """Test that bitstream path falls back to file search if not in output."""
        project = tmp_path / "test.xpr"
        project.touch()

        # Create completed implementation run with bitstream file
        runs_dir = tmp_path / "test.runs"
        runs_dir.mkdir()
        synth_dir = runs_dir / "synth_1"
        synth_dir.mkdir()
        (synth_dir / ".vivado.begin.rst").touch()
        (synth_dir / ".vivado.end.rst").touch()
        (synth_dir / "runme.log").write_text("synth_design Complete!")
        impl_dir = runs_dir / "impl_1"
        impl_dir.mkdir()
        (impl_dir / ".vivado.begin.rst").touch()
        (impl_dir / ".vivado.end.rst").touch()
        bitstream_file = impl_dir / "design.bit"
        bitstream_file.touch()

        mock_install = VivadoInstallation(
            version="2023.2",
            path=tmp_path / "Vivado" / "2023.2",
            executable=tmp_path / "Vivado" / "2023.2" / "bin" / "vivado",
        )

        # Output without BITSTREAM_FILE marker
        mock_process = MagicMock()
        mock_process.returncode = 0
        mock_process.communicate = AsyncMock(
            return_value=(b"Bitstream generation completed successfully\n", b"")
        )

        with (
            patch(
                "vivado_mcp.vivado.build.get_default_vivado",
                return_value=mock_install,
            ),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ),
        ):
            result = await run_bitstream_generation(project)
            assert result.success is True
            # Should find the bitstream file through fallback search
            assert result.bitstream_path == str(bitstream_file)
