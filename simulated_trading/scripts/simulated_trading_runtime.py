#!/usr/bin/env python3
"""Runtime defaults and paths for the simulated trading service."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18767
APP_NAME = "stock_skill_simulated_trading"
DB_FILENAME = "stock_skill_paper_trading.db"
LOG_FILENAME = "service.log"
PID_FILENAME = "service.pid"
LAUNCH_AGENT_LABEL = "ai.openclaw.stock-skill-simulated-trading"


def get_app_data_dir() -> Path:
    custom = os.environ.get("STOCK_SKILL_SIMULATED_TRADING_HOME") or os.environ.get("SIMULATED_TRADING_HOME")
    if custom:
        return Path(custom).expanduser()
    home = Path.home()
    if os.name == "posix" and "darwin" in os.uname().sysname.lower():
        base = home / "Library" / "Application Support"
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg).expanduser() if xdg else home / ".local" / "share"
    return base / APP_NAME


def get_default_db_path() -> Path:
    return get_app_data_dir() / DB_FILENAME


def get_default_log_path() -> Path:
    return get_app_data_dir() / LOG_FILENAME


def get_default_pid_path() -> Path:
    return get_app_data_dir() / PID_FILENAME


def get_launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def ensure_runtime_dir(path: Path | None = None) -> Path:
    target = path or get_app_data_dir()
    target.mkdir(parents=True, exist_ok=True)
    return target
