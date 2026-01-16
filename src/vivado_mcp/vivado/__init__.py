"""Vivado interaction modules."""

from vivado_mcp.vivado.detection import (
    VivadoInstallation,
    detect_vivado_installations,
    get_default_vivado,
)
from vivado_mcp.vivado.session import (
    SessionInfo,
    SessionManager,
    SessionState,
    TclCommandResult,
    TclSession,
    get_session_manager,
    run_tcl_command_with_fallback,
)

__all__ = [
    "VivadoInstallation",
    "detect_vivado_installations",
    "get_default_vivado",
    "SessionInfo",
    "SessionManager",
    "SessionState",
    "TclCommandResult",
    "TclSession",
    "get_session_manager",
    "run_tcl_command_with_fallback",
]
