"""Resolve base_data scripts directory for cross-skill imports."""

from __future__ import annotations

import sys
from pathlib import Path

from simulated_trading_path import repo_root

_BASE_DATA_SCRIPTS = repo_root() / "base_data" / "scripts"


def base_data_scripts_dir() -> Path:
    return _BASE_DATA_SCRIPTS


def ensure_base_data_on_path() -> Path:
    scripts_dir = _BASE_DATA_SCRIPTS
    if not scripts_dir.is_dir():
        raise FileNotFoundError(f"base_data scripts not found: {scripts_dir}")
    scripts_str = str(scripts_dir)
    if scripts_str not in sys.path:
        sys.path.insert(0, scripts_str)
    return scripts_dir
