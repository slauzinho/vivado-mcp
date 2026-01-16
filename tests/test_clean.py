"""Tests for Vivado clean module."""

from __future__ import annotations

from pathlib import Path

from vivado_mcp.vivado.clean import (
    VIVADO_OUTPUT_DIRS,
    CleanResult,
    _validate_project_path,
    clean_build_outputs,
)


class TestCleanResult:
    """Tests for CleanResult dataclass."""

    def test_to_dict_success(self) -> None:
        result = CleanResult(
            success=True,
            project_path="/path/to/project",
            cleaned_directories=[".runs", ".cache"],
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["project_path"] == "/path/to/project"
        assert d["cleaned_directories"] == [".runs", ".cache"]
        assert d["cleaned_count"] == 2
        assert d["errors"] == []

    def test_to_dict_with_errors(self) -> None:
        result = CleanResult(
            success=False,
            project_path="/path/to/project",
            cleaned_directories=[".runs"],
            errors=["Failed to remove .cache: Permission denied"],
        )
        d = result.to_dict()
        assert d["success"] is False
        assert d["cleaned_count"] == 1
        assert len(d["errors"]) == 1

    def test_to_dict_empty(self) -> None:
        result = CleanResult(
            success=True,
            project_path="/path/to/project",
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["cleaned_directories"] == []
        assert d["cleaned_count"] == 0


class TestValidateProjectPath:
    """Tests for project path validation."""

    def test_valid_xpr_file(self, tmp_path: Path) -> None:
        project = tmp_path / "test.xpr"
        project.touch()
        path, error = _validate_project_path(project)
        assert error is None
        assert path == tmp_path

    def test_valid_directory(self, tmp_path: Path) -> None:
        path, error = _validate_project_path(tmp_path)
        assert error is None
        assert path == tmp_path

    def test_directory_with_xpr(self, tmp_path: Path) -> None:
        project = tmp_path / "test.xpr"
        project.touch()
        path, error = _validate_project_path(tmp_path)
        assert error is None
        assert path == tmp_path

    def test_invalid_file_extension(self, tmp_path: Path) -> None:
        invalid_file = tmp_path / "design.v"
        invalid_file.touch()
        path, error = _validate_project_path(invalid_file)
        assert error is not None
        assert "Expected .xpr project file" in error

    def test_nonexistent_path(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "nonexistent"
        path, error = _validate_project_path(nonexistent)
        assert error is not None
        assert "not found" in error


class TestCleanBuildOutputs:
    """Tests for the main clean function."""

    def test_clean_existing_directories(self, tmp_path: Path) -> None:
        """Test cleaning existing Vivado output directories."""
        # Create some output directories
        (tmp_path / ".runs").mkdir()
        (tmp_path / ".cache").mkdir()
        (tmp_path / ".gen").mkdir()

        # Create a project file
        (tmp_path / "test.xpr").touch()

        result = clean_build_outputs(tmp_path / "test.xpr")

        assert result.success is True
        assert ".runs" in result.cleaned_directories
        assert ".cache" in result.cleaned_directories
        assert ".gen" in result.cleaned_directories
        assert not (tmp_path / ".runs").exists()
        assert not (tmp_path / ".cache").exists()
        assert not (tmp_path / ".gen").exists()

    def test_clean_with_directory_path(self, tmp_path: Path) -> None:
        """Test cleaning using a directory path instead of .xpr file."""
        (tmp_path / ".runs").mkdir()
        (tmp_path / ".hw").mkdir()

        result = clean_build_outputs(tmp_path)

        assert result.success is True
        assert ".runs" in result.cleaned_directories
        assert ".hw" in result.cleaned_directories

    def test_preserves_source_files(self, tmp_path: Path) -> None:
        """Test that source files are preserved during cleaning."""
        # Create source files and directories
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "design.v").touch()
        (tmp_path / "constraints.xdc").touch()
        (tmp_path / "test.xpr").touch()

        # Create output directories
        (tmp_path / ".runs").mkdir()
        (tmp_path / ".cache").mkdir()

        result = clean_build_outputs(tmp_path)

        assert result.success is True
        # Source files should be preserved
        assert src_dir.exists()
        assert (src_dir / "design.v").exists()
        assert (tmp_path / "constraints.xdc").exists()
        assert (tmp_path / "test.xpr").exists()

    def test_clean_empty_project(self, tmp_path: Path) -> None:
        """Test cleaning when no output directories exist."""
        (tmp_path / "test.xpr").touch()

        result = clean_build_outputs(tmp_path)

        assert result.success is True
        assert result.cleaned_directories == []

    def test_clean_all_vivado_dirs(self, tmp_path: Path) -> None:
        """Test that all default Vivado directories are cleaned."""
        # Create all default output directories
        for dir_name in VIVADO_OUTPUT_DIRS:
            (tmp_path / dir_name).mkdir()

        result = clean_build_outputs(tmp_path)

        assert result.success is True
        assert len(result.cleaned_directories) == len(VIVADO_OUTPUT_DIRS)
        for dir_name in VIVADO_OUTPUT_DIRS:
            assert dir_name in result.cleaned_directories
            assert not (tmp_path / dir_name).exists()

    def test_clean_nested_contents(self, tmp_path: Path) -> None:
        """Test that nested contents in output directories are cleaned."""
        runs_dir = tmp_path / ".runs"
        runs_dir.mkdir()
        synth_dir = runs_dir / "synth_1"
        synth_dir.mkdir()
        (synth_dir / "design.dcp").touch()
        (synth_dir / "runme.log").touch()

        result = clean_build_outputs(tmp_path)

        assert result.success is True
        assert ".runs" in result.cleaned_directories
        assert not runs_dir.exists()

    def test_invalid_project_path(self, tmp_path: Path) -> None:
        """Test handling of non-existent project path."""
        result = clean_build_outputs(tmp_path / "nonexistent")

        assert result.success is False
        assert len(result.errors) == 1
        assert "not found" in result.errors[0]

    def test_invalid_file_extension(self, tmp_path: Path) -> None:
        """Test handling of invalid file type."""
        invalid_file = tmp_path / "design.v"
        invalid_file.touch()

        result = clean_build_outputs(invalid_file)

        assert result.success is False
        assert len(result.errors) == 1
        assert "Expected .xpr project file" in result.errors[0]

    def test_clean_with_additional_dirs(self, tmp_path: Path) -> None:
        """Test cleaning with additional custom directories."""
        (tmp_path / ".runs").mkdir()
        (tmp_path / "custom_output").mkdir()
        (tmp_path / "build").mkdir()

        result = clean_build_outputs(
            tmp_path,
            additional_dirs=["custom_output", "build"],
        )

        assert result.success is True
        assert ".runs" in result.cleaned_directories
        assert "custom_output" in result.cleaned_directories
        assert "build" in result.cleaned_directories
        assert not (tmp_path / ".runs").exists()
        assert not (tmp_path / "custom_output").exists()
        assert not (tmp_path / "build").exists()

    def test_additional_dirs_not_exists(self, tmp_path: Path) -> None:
        """Test that non-existent additional directories are ignored."""
        result = clean_build_outputs(
            tmp_path,
            additional_dirs=["nonexistent_dir"],
        )

        assert result.success is True
        assert "nonexistent_dir" not in result.cleaned_directories

    def test_result_includes_project_path(self, tmp_path: Path) -> None:
        """Test that result includes the resolved project path."""
        project_dir = tmp_path / "my_project"
        project_dir.mkdir()
        project_file = project_dir / "test.xpr"
        project_file.touch()

        result = clean_build_outputs(project_file)

        # Should resolve to the project directory, not the file
        assert str(project_dir) in result.project_path
