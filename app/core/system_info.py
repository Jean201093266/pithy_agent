"""Detect runtime OS / shell / environment and expose as a context string for LLM prompts."""
from __future__ import annotations

import os
import platform
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def get_system_info() -> dict[str, str]:
    """Return a dict with OS, shell, architecture and common paths."""
    system = platform.system()           # Windows / Linux / Darwin
    release = platform.release()
    arch = platform.machine()            # AMD64 / x86_64 / arm64

    # Determine default shell
    if system == "Windows":
        shell = os.environ.get("COMSPEC", "cmd.exe")
        # Detect PowerShell
        if "powershell" in os.environ.get("PSModulePath", "").lower():
            shell = "powershell"
        shell_name = "PowerShell" if "powershell" in shell.lower() else "CMD"
    else:
        shell = os.environ.get("SHELL", "/bin/sh")
        shell_name = Path(shell).name  # bash / zsh / fish / sh

    # Common user directories
    home = str(Path.home())
    desktop = str(Path.home() / "Desktop")
    if system == "Windows":
        desktop = str(Path.home() / "Desktop")
        # Try OneDrive desktop
        onedrive_desktop = Path.home() / "OneDrive" / "桌面"
        if not Path(desktop).exists() and onedrive_desktop.exists():
            desktop = str(onedrive_desktop)
        # Also try Chinese name
        cn_desktop = Path.home() / "桌面"
        if not Path(desktop).exists() and cn_desktop.exists():
            desktop = str(cn_desktop)
    elif system == "Darwin":
        desktop = str(Path.home() / "Desktop")
    # Linux usually ~/Desktop

    path_sep = ";" if system == "Windows" else ":"
    line_sep = "\\r\\n" if system == "Windows" else "\\n"

    return {
        "os": system,
        "os_release": release,
        "arch": arch,
        "shell": shell_name,
        "home": home,
        "desktop": desktop,
        "path_separator": path_sep,
        "line_separator": line_sep,
    }


@lru_cache(maxsize=1)
def get_system_context_string() -> str:
    """One-liner context string to embed in LLM prompts."""
    info = get_system_info()
    return (
        f"System: {info['os']} {info['os_release']} ({info['arch']}), "
        f"Shell: {info['shell']}, "
        f"Home: {info['home']}, "
        f"Desktop: {info['desktop']}"
    )

