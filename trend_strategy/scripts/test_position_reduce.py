#!/usr/bin/env python3
"""Unit tests for tiered reduce signal logic."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from strategy_lab.position_reduce import (  # noqa: E402
    REASON_CLEAR_MA20,
    REASON_REDUCE_MA10,
    REASON_REDUCE_MA5,
    REASON_REDUCE_RSI,
    empty_reduce_state,
    evaluate_reduce_signal,
)


def _row(close: float, ma5: float, ma10: float, ma20: float, rsi: float) -> pd.Series:
    return pd.Series(
        {
            "time": pd.Timestamp("2026-06-20"),
            "close": close,
            "ma_5": ma5,
            "ma_10": ma10,
            "ma_20": ma20,
            "rsi_14": rsi,
        }
    )


def test_clear_on_ma20_break() -> None:
    sig = evaluate_reduce_signal(_row(9.0, 10.0, 10.5, 9.5, 50.0), empty_reduce_state(), {"exit_rsi": 74})
    assert sig is not None
    assert sig["action"] == "clear"
    assert sig["reason"] == REASON_CLEAR_MA20


def test_ma10_reduce_once() -> None:
    state = empty_reduce_state()
    sig = evaluate_reduce_signal(_row(9.8, 10.0, 10.0, 9.0, 50.0), state, {"exit_rsi": 74})
    assert sig is not None
    assert sig["reason"] == REASON_REDUCE_MA10
    assert sig["reduce_pct_label"] == "1/2"
    assert sig["reduce_basis"] == "remaining_position"

    state["ma10_reduce_done"] = True
    assert evaluate_reduce_signal(_row(9.99, 9.0, 10.0, 8.0, 50.0), state, {"exit_rsi": 74}) is None


def test_rsi_reduce_above_ma5() -> None:
    sig = evaluate_reduce_signal(_row(10.2, 10.0, 9.5, 9.0, 76.0), empty_reduce_state(), {"exit_rsi": 74})
    assert sig is not None
    assert sig["reason"] == REASON_REDUCE_RSI
    assert sig["reduce_pct_label"] == "1/3"


def test_rsi_blocks_ma5_when_hot() -> None:
    sig = evaluate_reduce_signal(_row(9.8, 10.0, 9.5, 9.0, 76.0), empty_reduce_state(), {"exit_rsi": 74})
    assert sig is None


def test_ma5_reduce_when_rsi_not_hot() -> None:
    sig = evaluate_reduce_signal(_row(9.8, 10.0, 9.5, 9.0, 60.0), empty_reduce_state(), {"exit_rsi": 74})
    assert sig is not None
    assert sig["reason"] == REASON_REDUCE_MA5


def test_ma10_priority_over_rsi_and_ma5() -> None:
    sig = evaluate_reduce_signal(_row(9.8, 10.0, 10.0, 9.0, 76.0), empty_reduce_state(), {"exit_rsi": 74})
    assert sig is not None
    assert sig["reason"] == REASON_REDUCE_MA10


if __name__ == "__main__":
    test_clear_on_ma20_break()
    test_ma10_reduce_once()
    test_rsi_reduce_above_ma5()
    test_rsi_blocks_ma5_when_hot()
    test_ma5_reduce_when_rsi_not_hot()
    test_ma10_priority_over_rsi_and_ma5()
    print("ok")
