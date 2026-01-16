"""Tests for Vivado build module."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vivado_mcp.vivado.build import (
    BuildMessage,
    BuildResult,
    _generate_build_tcl,
    _validate_project_path,
    parse_vivado_output,
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
        assert "set_msg_config -severity ERROR -stop true" in tcl

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

    def test_stop_on_error_disabled(self, tmp_path: Path) -> None:
        project = tmp_path / "test.xpr"
        tcl = _generate_build_tcl(project, stop_on_error=False)
        assert "set_msg_config -severity ERROR -stop true" not in tcl


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
