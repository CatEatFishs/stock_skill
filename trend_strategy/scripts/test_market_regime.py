#!/usr/bin/env python3
"""Unit tests for market regime gating."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from strategy_lab.market_regime import evaluate_index_bar, evaluate_market_regime  # noqa: E402


class _Provider:
    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames

    def get_history(self, symbol: str, count: int = 60) -> pd.DataFrame:
        return self.frames[symbol].tail(count).reset_index(drop=True)


def _df(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "time": pd.date_range("2026-01-01", periods=len(closes), freq="D"),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1.0] * len(closes),
        }
    )


def test_index_bar_above_ma_and_rsi() -> None:
    closes = [100.0 + i * 0.5 for i in range(30)]
    snap = evaluate_index_bar(_df(closes), -1, ma_period=20, rsi_period=14, rsi_floor=40.0)
    assert snap is not None
    assert snap["above_ma"] is True
    assert snap["rsi_ok"] is True


def test_market_regime_blocks_when_below_ma() -> None:
    closes = [100.0] * 25 + [90.0] * 5
    provider = _Provider({"000300": _df(closes), "000852": _df(closes)})
    out = evaluate_market_regime(provider, {"hs300": "000300", "zz1000": "000852"}, bar_idx=-1)
    assert out["allow_new_buys"] is False
    assert "index_below_ma20" in out["block_reasons"]


if __name__ == "__main__":
    test_index_bar_above_ma_and_rsi()
    test_market_regime_blocks_when_below_ma()
    print("ok")
