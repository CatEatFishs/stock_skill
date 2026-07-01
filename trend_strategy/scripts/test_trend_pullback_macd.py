#!/usr/bin/env python3
"""Unit tests for trend_pullback MACD histogram expansion."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from strategy_lab.strategies import trend_pullback  # noqa: E402


def _make_uptrend_df(n: int = 80) -> pd.DataFrame:
    rng = np.linspace(10.0, 20.0, n)
    noise = np.sin(np.linspace(0, 6, n)) * 0.05
    closes = rng + noise
    return pd.DataFrame(
        {
            "time": pd.date_range("2026-01-01", periods=n, freq="D"),
            "open": closes,
            "high": closes * 1.01,
            "low": closes * 0.99,
            "close": closes,
            "volume": np.full(n, 1e6),
        }
    )


def test_macd_hist_expanding_required_for_entry() -> None:
    df = _make_uptrend_df()
    params = {
        "fast": 8,
        "slow": 20,
        "pullback_ceiling": 1.02,
        "bull_rsi_low": 0,
        "bull_rsi_high": 100,
        "macd_fast": 12,
        "macd_slow": 26,
        "macd_signal": 9,
    }
    out = trend_pullback(df, params)
    assert "entry" in out.columns
    if bool(out.iloc[-1]["entry"]):
        assert float(out.iloc[-1]["macd_hist"]) > float(out.iloc[-2]["macd_hist"])


if __name__ == "__main__":
    test_macd_hist_expanding_required_for_entry()
    print("ok")
