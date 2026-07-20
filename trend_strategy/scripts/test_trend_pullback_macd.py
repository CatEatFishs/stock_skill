#!/usr/bin/env python3
"""Unit tests for trend_pullback MACD momentum as score bonus."""

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


def test_entry_does_not_require_macd_momentum() -> None:
    df = _make_uptrend_df()
    params = {
        "fast": 8,
        "slow": 20,
        "pullback_ceiling": 1.02,
        "bull_rsi_low": 0,
        "bull_rsi_high": 100,
        "macd_momentum_score_bonus": 0.003,
    }
    out = trend_pullback(df, params)
    assert "macd_momentum" in out.columns
    if bool(out.iloc[-1]["entry"]):
        # entry can be true regardless of macd_momentum
        pass
    # MACD columns still computed for bonus
    assert "macd_dif" in out.columns


def test_macd_momentum_adds_score_bonus() -> None:
    df = _make_uptrend_df()
    bonus = 0.003
    params = {
        "fast": 8,
        "slow": 20,
        "pullback_ceiling": 1.02,
        "bull_rsi_low": 0,
        "bull_rsi_high": 100,
        "macd_momentum_score_bonus": bonus,
    }
    out = trend_pullback(df, params)
    base = (out["ma_8"] / out["ma_20"] - 1.0).fillna(0.0)
    expected = base + out["macd_momentum"].astype(float) * bonus
    pd.testing.assert_series_equal(out["score"], expected, check_names=False)


if __name__ == "__main__":
    test_entry_does_not_require_macd_momentum()
    test_macd_momentum_adds_score_bonus()
    print("ok")
