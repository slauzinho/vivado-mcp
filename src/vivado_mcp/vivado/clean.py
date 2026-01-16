"""Vivado build cleanup module.

This module provides functionality to clean Vivado build output directories
to allow fresh rebuilds without stale artifacts.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

# Default Vivado output directories to clean
# These are created by Vivado during project creation and builds
VIVADO_OUTPUT_DIRS = [
    ".runs",  # Synthesis and implementation run directories
    ".cache",  # Vivado cache files
    ".gen",  # Generated files
    ".hw",  # Hardware platform files
    ".ip_user_files",  # IP user files
]


@dataclass
class CleanResult:
    """Represents the result of a clean operation."""

    success: bool
    project_path: str
    cleaned_directories: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Convert to dictionary for JSON serialization."""
        return {
            "success": self.success,
            "project_path": self.project_path,
            "cleaned_directories": self.cleaned_directories,
            "cleaned_count": len(self.cleaned_directories),
            "errors": self.errors,
        }


def _validate_project_path(project_path: str | Path) -> tuple[Path, str | None]:
    """Validate the project path and resolve the project directory.

    Args:
        project_path: Path to the project file (.xpr) or project directory

    Returns:
        Tuple of (project_directory, error_message)
        error_message is None if validation passed
    """
    path = Path(project_path).resolve()

    # If a file is provided, use its parent directory
    if path.is_file():
        if path.suffix.lower() != ".xpr":
            return path.parent, f"Expected .xpr project file, got: {path.suffix}"
        return path.parent, None

    # If a directory is provided, check if it contains a .xpr file
    if path.is_dir():
        xpr_files = list(path.glob("*.xpr"))
        if not xpr_files:
            # Still allow cleaning even without .xpr file
            # The directory might have leftover build artifacts
            pass
        return path, None

    # Path doesn't exist
    return path, f"Project path not found: {path}"


def clean_build_outputs(
    project_path: str | Path,
    additional_dirs: list[str] | None = None,
) -> CleanResult:
    """Clean Vivado build output directories.

    Removes the default Vivado output directories (.runs/, .cache/, .gen/,
    .hw/, .ip_user_files/) while preserving source files, constraints,
    and project configuration.

    Args:
        project_path: Path to the Vivado project file (.xpr) or project directory
        additional_dirs: Optional list of additional directories to clean

    Returns:
        CleanResult containing success status and list of cleaned directories
    """
    # Validate project path
    project_dir, error = _validate_project_path(project_path)

    if error:
        return CleanResult(
            success=False,
            project_path=str(project_path),
            errors=[error],
        )

    # Build list of directories to clean
    dirs_to_clean = list(VIVADO_OUTPUT_DIRS)
    if additional_dirs:
        dirs_to_clean.extend(additional_dirs)

    cleaned_directories: list[str] = []
    errors: list[str] = []

    for dir_name in dirs_to_clean:
        dir_path = project_dir / dir_name
        if dir_path.exists():
            try:
                if dir_path.is_dir():
                    shutil.rmtree(dir_path)
                    cleaned_directories.append(dir_name)
                else:
                    # Handle case where it's a file (shouldn't happen, but be safe)
                    dir_path.unlink()
                    cleaned_directories.append(dir_name)
            except OSError as e:
                errors.append(f"Failed to remove {dir_name}: {e}")

    # Determine overall success
    success = len(errors) == 0

    return CleanResult(
        success=success,
        project_path=str(project_dir),
        cleaned_directories=cleaned_directories,
        errors=errors,
    )
