"""Resolve simulated_trading scripts directory for cross-skill imports."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent.parent
_SIMULATED_SCRIPTS = _REPO_ROOT / "simulated_trading" / "scripts"


def repo_root() -> Path:
    return _REPO_ROOT


def simulated_trading_scripts_dir() -> Path:
    return _SIMULATED_SCRIPTS


def ensure_simulated_trading_on_path() -> Path:
    scripts_dir = _SIMULATED_SCRIPTS
    if not scripts_dir.is_dir():
        raise FileNotFoundError(f"simulated_trading scripts not found: {scripts_dir}")
    scripts_str = str(scripts_dir)
    if scripts_str not in sys.path:
        sys.path.insert(0, scripts_str)
    return scripts_dir
