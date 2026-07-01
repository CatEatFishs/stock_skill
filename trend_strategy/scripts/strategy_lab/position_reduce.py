"""Tiered position reduction signals for held symbols.

Each reduce ratio applies to the **remaining position** at trigger time
(e.g. 1/2 leaves half; a later 1/3 reduces one-third of what is left).
The engine records which tiers already fired; it does not auto-submit orders.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .indicators import add_ma, add_rsi


REDUCE_MA_PERIODS = (5, 10, 20)
REDUCE_BASIS = "remaining_position"

REASON_CLEAR_MA20 = "break_ma20"
REASON_REDUCE_MA10 = "break_ma10"
REASON_REDUCE_RSI = "rsi_overbought_above_ma5"
REASON_REDUCE_MA5 = "break_ma5"

STATE_FLAG_BY_REASON = {
    REASON_REDUCE_RSI: "rsi_reduce_done",
    REASON_REDUCE_MA5: "ma5_reduce_done",
    REASON_REDUCE_MA10: "ma10_reduce_done",
}


def empty_reduce_state() -> dict[str, bool]:
    return {
        "rsi_reduce_done": False,
        "ma5_reduce_done": False,
        "ma10_reduce_done": False,
    }


def enrich_position_management(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    rsi_period = int(params.get("rsi_period", 14))
    return add_rsi(add_ma(df, list(REDUCE_MA_PERIODS)), rsi_period)


def evaluate_reduce_signal(row: pd.Series, state: dict[str, bool], params: dict) -> dict[str, Any] | None:
    """Return one reduce/clear signal for the bar, or None.

    Priority (same bar, pick the first that applies and is not yet done):
    1. break ma_20 -> clear
    2. break ma_10 -> reduce 1/2 (once)
    3. RSI > exit_rsi and close > ma_5 -> reduce 1/3 (once)
    4. break ma_5 -> reduce 1/3 (once), skipped when RSI > exit_rsi
    """
    rsi_period = int(params.get("rsi_period", 14))
    rsi_key = f"rsi_{rsi_period}"
    exit_rsi = float(params.get("exit_rsi", 74))

    close = float(row["close"])
    ma5 = float(row["ma_5"])
    ma10 = float(row["ma_10"])
    ma20 = float(row["ma_20"])
    rsi = float(row[rsi_key]) if rsi_key in row.index and pd.notna(row[rsi_key]) else None

    if pd.isna(ma5) or pd.isna(ma10) or pd.isna(ma20):
        return None

    if close < ma20:
        return _signal(row, action="clear", ratio=1.0, reason=REASON_CLEAR_MA20, rsi=rsi)

    if close < ma10 and not bool(state.get("ma10_reduce_done")):
        return _signal(row, action="reduce", ratio=0.5, reason=REASON_REDUCE_MA10, rsi=rsi)

    if rsi is not None and rsi > exit_rsi and close > ma5 and not bool(state.get("rsi_reduce_done")):
        return _signal(row, action="reduce", ratio=1.0 / 3.0, reason=REASON_REDUCE_RSI, rsi=rsi)

    if close < ma5 and not bool(state.get("ma5_reduce_done")):
        if rsi is not None and rsi > exit_rsi:
            return None
        return _signal(row, action="reduce", ratio=1.0 / 3.0, reason=REASON_REDUCE_MA5, rsi=rsi)

    return None


def _signal(
    row: pd.Series,
    *,
    action: str,
    ratio: float,
    reason: str,
    rsi: float | None,
) -> dict[str, Any]:
    ts = row["time"]
    date_s = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
    pct_label = "清仓" if action == "clear" else _ratio_label(ratio)
    return {
        "action": action,
        "reduce_ratio": round(ratio, 6),
        "reduce_pct_label": pct_label,
        "reduce_basis": REDUCE_BASIS,
        "reason": reason,
        "state_flag": STATE_FLAG_BY_REASON.get(reason),
        "signal_bar": {
            "date": date_s,
            "close": round(float(row["close"]), 4),
            "ma_5": round(float(row["ma_5"]), 4),
            "ma_10": round(float(row["ma_10"]), 4),
            "ma_20": round(float(row["ma_20"]), 4),
            "rsi": None if rsi is None else round(rsi, 4),
        },
    }


def _ratio_label(ratio: float) -> str:
    if abs(ratio - 1.0 / 3.0) < 1e-6:
        return "1/3"
    if abs(ratio - 0.5) < 1e-6:
        return "1/2"
    if abs(ratio - 1.0) < 1e-6:
        return "清仓"
    return f"{ratio:.2%}"
