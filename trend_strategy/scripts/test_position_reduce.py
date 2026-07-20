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
    REASON_EARLY_HOLD_REDUCE_RISK,
    REASON_HARD_STOP,
    REASON_REDUCE_MA10,
    REASON_REDUCE_MA5,
    REASON_REDUCE_RSI,
    REASON_SWING_BREAK_MA10,
    REASON_SWING_PROFIT_MA5,
    detect_position_mode,
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


def _row_with_mode(
    close: float,
    ma5: float,
    ma10: float,
    ma20: float,
    rsi: float,
    *,
    ret20: float,
    slope5: float,
    volume_ratio: float,
) -> pd.Series:
    out = _row(close, ma5, ma10, ma20, rsi)
    out["ret_20"] = ret20
    out["ma20_slope_5"] = slope5
    out["volume_ratio_20"] = volume_ratio
    return out


def test_clear_on_ma20_break() -> None:
    sig = evaluate_reduce_signal(_row(9.0, 10.0, 10.5, 9.5, 50.0), empty_reduce_state(), {"exit_rsi": 74})
    assert sig is not None
    assert sig["action"] == "clear"
    assert sig["reason"] == REASON_CLEAR_MA20


def test_ma10_warns_without_reducing() -> None:
    state = empty_reduce_state()
    sig = evaluate_reduce_signal(_row(9.8, 10.0, 10.0, 9.0, 50.0), state, {"exit_rsi": 74})
    assert sig is not None
    assert sig["reason"] == REASON_REDUCE_MA10
    assert sig["action"] == "watch"
    assert sig["reduce_ratio"] == 0.0
    assert sig["reduce_pct_label"] == "减仓风险提示"
    assert sig["reduce_basis"] == "remaining_position"

    state["ma10_reduce_done"] = True
    sig = evaluate_reduce_signal(_row(9.99, 9.0, 10.0, 8.0, 50.0), state, {"exit_rsi": 74})
    assert sig is not None
    assert sig["reason"] == REASON_REDUCE_MA10
    assert sig["action"] == "watch"


def test_trend_mode_ma10_warns_without_reducing() -> None:
    sig = evaluate_reduce_signal(
        _row_with_mode(9.8, 10.0, 10.0, 9.0, 50.0, ret20=0.30, slope5=0.05, volume_ratio=1.1),
        empty_reduce_state(),
        {"exit_rsi": 74},
    )
    assert sig is not None
    assert sig["position_mode"] == "trend"
    assert sig["reason"] == REASON_REDUCE_MA10
    assert sig["action"] == "watch"


def test_recent_trend_context_keeps_trend_mode() -> None:
    row = _row_with_mode(14.8, 15.0, 14.0, 13.0, 60.0, ret20=0.12, slope5=0.01, volume_ratio=0.7)
    row["trend_mode_recent"] = True
    mode = detect_position_mode(row, {"exit_rsi": 74})
    assert mode["mode"] == "trend"
    assert mode["reason"] == "trend_context_recent"


def test_swing_mode_ma10_clears() -> None:
    sig = evaluate_reduce_signal(
        _row_with_mode(9.8, 10.0, 10.0, 9.0, 50.0, ret20=0.08, slope5=0.01, volume_ratio=0.8),
        empty_reduce_state(),
        {"exit_rsi": 74},
        avg_cost=9.0,
    )
    assert sig is not None
    assert sig["position_mode"] == "swing"
    assert sig["reason"] == REASON_SWING_BREAK_MA10
    assert sig["action"] == "clear"


def test_swing_mode_profit_break_ma5_reduces() -> None:
    sig = evaluate_reduce_signal(
        _row_with_mode(11.6, 12.0, 10.5, 9.0, 60.0, ret20=0.08, slope5=0.01, volume_ratio=0.8),
        empty_reduce_state(),
        {"exit_rsi": 74, "swing_profit_ma5_pct": 0.15},
        avg_cost=10.0,
    )
    assert sig is not None
    assert sig["position_mode"] == "swing"
    assert sig["reason"] == REASON_SWING_PROFIT_MA5
    assert sig["action"] == "reduce"
    assert sig["reduce_ratio"] == 0.5


def test_early_hold_ma10_only_warns() -> None:
    sig = evaluate_reduce_signal(
        _row(9.8, 10.0, 10.0, 9.0, 50.0),
        empty_reduce_state(),
        {"exit_rsi": 74, "early_hold_protect_days": 3},
        holding_trading_days=2,
        avg_cost=10.0,
    )
    assert sig is not None
    assert sig["action"] == "watch"
    assert sig["reason"] == REASON_EARLY_HOLD_REDUCE_RISK
    assert sig["blocked_reason"] == REASON_REDUCE_MA10
    assert sig["reduce_pct_label"] == "减仓风险提示"


def test_early_hold_ma20_still_clears() -> None:
    sig = evaluate_reduce_signal(
        _row(9.3, 10.0, 10.0, 9.5, 50.0),
        empty_reduce_state(),
        {"exit_rsi": 74, "early_hold_protect_days": 3},
        holding_trading_days=2,
        avg_cost=10.0,
    )
    assert sig is not None
    assert sig["action"] == "clear"
    assert sig["reason"] == REASON_CLEAR_MA20


def test_hard_stop_priority() -> None:
    sig = evaluate_reduce_signal(
        _row(9.1, 10.0, 10.0, 8.0, 50.0),
        empty_reduce_state(),
        {"exit_rsi": 74, "hard_stop_pct": 0.08},
        holding_trading_days=2,
        avg_cost=10.0,
    )
    assert sig is not None
    assert sig["action"] == "clear"
    assert sig["reason"] == REASON_HARD_STOP


def test_rsi_watch_above_ma5() -> None:
    sig = evaluate_reduce_signal(_row(10.2, 10.0, 9.5, 9.0, 76.0), empty_reduce_state(), {"exit_rsi": 74})
    assert sig is not None
    assert sig["reason"] == REASON_REDUCE_RSI
    assert sig["action"] == "watch"
    assert sig["reduce_ratio"] == 0.0
    assert sig["reduce_pct_label"] == "可选卖出部分"


def test_rsi_blocks_ma5_when_hot() -> None:
    sig = evaluate_reduce_signal(_row(9.8, 10.0, 9.5, 9.0, 76.0), empty_reduce_state(), {"exit_rsi": 74})
    assert sig is None


def test_ma5_warns_when_rsi_not_hot() -> None:
    sig = evaluate_reduce_signal(_row(9.8, 10.0, 9.5, 9.0, 60.0), empty_reduce_state(), {"exit_rsi": 74})
    assert sig is not None
    assert sig["reason"] == REASON_REDUCE_MA5
    assert sig["action"] == "watch"
    assert sig["reduce_ratio"] == 0.0
    assert sig["reduce_pct_label"] == "减仓风险提示"


def test_ma10_priority_over_rsi_and_ma5() -> None:
    sig = evaluate_reduce_signal(_row(9.8, 10.0, 10.0, 9.0, 76.0), empty_reduce_state(), {"exit_rsi": 74})
    assert sig is not None
    assert sig["reason"] == REASON_REDUCE_MA10
    assert sig["action"] == "watch"


if __name__ == "__main__":
    test_clear_on_ma20_break()
    test_ma10_warns_without_reducing()
    test_trend_mode_ma10_warns_without_reducing()
    test_recent_trend_context_keeps_trend_mode()
    test_swing_mode_ma10_clears()
    test_swing_mode_profit_break_ma5_reduces()
    test_early_hold_ma10_only_warns()
    test_early_hold_ma20_still_clears()
    test_hard_stop_priority()
    test_rsi_watch_above_ma5()
    test_rsi_blocks_ma5_when_hot()
    test_ma5_warns_when_rsi_not_hot()
    test_ma10_priority_over_rsi_and_ma5()
    print("ok")
