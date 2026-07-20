"""Position risk and exit signals for held symbols.

The exit layer is dual-mode:
- trend positions keep MA5/MA10 as risk warnings only.
- swing positions can actively reduce/clear when profit protection triggers.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .indicators import add_ma, add_rsi


REDUCE_MA_PERIODS = (5, 10, 20)
REDUCE_BASIS = "remaining_position"

REASON_CLEAR_MA20 = "break_ma20"
REASON_HARD_STOP = "hard_stop_8pct"
REASON_REDUCE_MA10 = "break_ma10"
REASON_REDUCE_RSI = "rsi_overbought_above_ma5"
REASON_REDUCE_MA5 = "break_ma5"
REASON_EARLY_HOLD_REDUCE_RISK = "early_hold_reduce_risk"
REASON_SWING_BREAK_MA10 = "swing_break_ma10"
REASON_SWING_PROFIT_MA5 = "swing_profit_break_ma5"
REASON_SWING_TRAILING_DD = "swing_trailing_8pct"
REASON_SWING_RSI_BEAR_VOLUME = "swing_rsi_bear_volume"

STATE_FLAG_BY_REASON = {
    REASON_REDUCE_RSI: "rsi_reduce_done",
}


def empty_reduce_state() -> dict[str, bool]:
    return {
        "rsi_reduce_done": False,
        "ma5_reduce_done": False,
        "ma10_reduce_done": False,
    }


def enrich_position_management(df: pd.DataFrame, params: dict) -> pd.DataFrame:
    rsi_period = int(params.get("rsi_period", 14))
    out = add_rsi(add_ma(df, list(REDUCE_MA_PERIODS)), rsi_period)
    out["ret_20"] = out["close"] / out["close"].shift(20) - 1.0
    out["ma20_slope_5"] = out["ma_20"] / out["ma_20"].shift(5) - 1.0
    if "volume" in out.columns:
        out["volume_ma20"] = out["volume"].rolling(20).mean()
        out["volume_ratio_20"] = out["volume"] / out["volume_ma20"].replace(0, pd.NA)
    else:
        out["volume_ma20"] = pd.NA
        out["volume_ratio_20"] = pd.NA
    ret_min = float(params.get("trend_mode_ret20_min", 0.25))
    slope_min = float(params.get("trend_mode_ma20_slope5_min", 0.02))
    volume_min = float(params.get("trend_mode_volume_ratio_min", 1.0))
    strong_ret = float(params.get("trend_mode_strong_ret20", 0.40))
    lookback = int(params.get("trend_mode_lookback_days", 15))
    volume_ok = out["volume_ratio_20"].isna() | (out["volume_ratio_20"] >= volume_min) | (out["ret_20"] >= strong_ret)
    out["trend_mode_raw"] = (
        (out["close"] > out["ma_20"])
        & (out["ret_20"] >= ret_min)
        & (out["ma20_slope_5"] >= slope_min)
        & volume_ok
    )
    out["trend_mode_recent"] = out["trend_mode_raw"].rolling(max(1, lookback), min_periods=1).max().fillna(False).astype(bool)
    return out


def detect_position_mode(row: pd.Series, params: dict) -> dict[str, Any]:
    """Classify a held stock as trend or swing using current daily context."""
    close = _float_or_none(row.get("close"))
    ma20 = _float_or_none(row.get("ma_20"))
    ret20 = _float_or_none(row.get("ret_20"))
    slope5 = _float_or_none(row.get("ma20_slope_5"))
    volume_ratio = _float_or_none(row.get("volume_ratio_20"))

    evidence = {
        "close_above_ma20": bool(close is not None and ma20 is not None and close > ma20),
        "ret_20": None if ret20 is None else round(ret20, 6),
        "ma20_slope_5": None if slope5 is None else round(slope5, 6),
        "volume_ratio_20": None if volume_ratio is None else round(volume_ratio, 6),
        "trend_mode_recent": bool(row.get("trend_mode_recent")) if "trend_mode_recent" in row.index else None,
    }
    if close is None or ma20 is None or ret20 is None or slope5 is None:
        return {"mode": "trend", "reason": "insufficient_mode_data", "evidence": evidence}

    if close > ma20 and bool(row.get("trend_mode_recent", False)):
        return {"mode": "trend", "reason": "trend_context_recent", "evidence": evidence}

    ret_min = float(params.get("trend_mode_ret20_min", 0.25))
    slope_min = float(params.get("trend_mode_ma20_slope5_min", 0.02))
    volume_min = float(params.get("trend_mode_volume_ratio_min", 1.0))
    strong_ret = float(params.get("trend_mode_strong_ret20", 0.40))
    volume_ok = volume_ratio is None or volume_ratio >= volume_min or ret20 >= strong_ret
    trend = bool(close > ma20 and ret20 >= ret_min and slope5 >= slope_min and volume_ok)
    return {
        "mode": "trend" if trend else "swing",
        "reason": "trend_context_matched" if trend else "swing_context",
        "evidence": evidence,
    }


def evaluate_reduce_signal(
    row: pd.Series,
    state: dict[str, bool],
    params: dict,
    *,
    holding_trading_days: int | None = None,
    avg_cost: float | None = None,
    peak_close: float | None = None,
) -> dict[str, Any] | None:
    """Return one watch/clear signal for the bar, or None.

    Priority (same bar, pick the first that applies and is not yet done):
    1. hard stop -> clear
    2. break ma_20 -> clear
    3. swing-mode profit protection -> clear/reduce
    4. trend-mode MA/RSI warnings -> watch only
    """
    rsi_period = int(params.get("rsi_period", 14))
    rsi_key = f"rsi_{rsi_period}"
    exit_rsi = float(params.get("exit_rsi", 74))
    mode_info = detect_position_mode(row, params)
    mode_extra = _mode_extra(mode_info)

    close = float(row["close"])
    open_px = _float_or_none(row.get("open"))
    ma5 = float(row["ma_5"])
    ma10 = float(row["ma_10"])
    ma20 = float(row["ma_20"])
    rsi = float(row[rsi_key]) if rsi_key in row.index and pd.notna(row[rsi_key]) else None
    volume = _float_or_none(row.get("volume"))
    volume_ma20 = _float_or_none(row.get("volume_ma20"))

    if pd.isna(ma5) or pd.isna(ma10) or pd.isna(ma20):
        return None

    hard_stop_pct = float(params.get("hard_stop_pct", 0.08))
    if avg_cost is not None and float(avg_cost) > 0 and close <= float(avg_cost) * (1.0 - hard_stop_pct):
        return _signal(row, action="clear", ratio=1.0, reason=REASON_HARD_STOP, rsi=rsi, extra=mode_extra)

    if close < ma20:
        return _signal(row, action="clear", ratio=1.0, reason=REASON_CLEAR_MA20, rsi=rsi, extra=mode_extra)

    protect_days = int(params.get("early_hold_protect_days", 3))
    in_early_hold = holding_trading_days is not None and 1 <= int(holding_trading_days) <= protect_days
    if in_early_hold:
        if close < ma10:
            return _signal(
                row,
                action="watch",
                ratio=0.0,
                reason=REASON_EARLY_HOLD_REDUCE_RISK,
                rsi=rsi,
                extra={
                    **mode_extra,
                    "blocked_reason": REASON_REDUCE_MA10,
                    "message": f"买入后前{protect_days}个交易日不触发MA10减仓，仅提示减仓风险",
                    "holding_trading_days": int(holding_trading_days),
                },
            )
        if close < ma5:
            return _signal(
                row,
                action="watch",
                ratio=0.0,
                reason=REASON_EARLY_HOLD_REDUCE_RISK,
                rsi=rsi,
                extra={
                    **mode_extra,
                    "blocked_reason": REASON_REDUCE_MA5,
                    "message": f"买入后前{protect_days}个交易日不触发MA5减仓，仅提示减仓风险",
                    "holding_trading_days": int(holding_trading_days),
                },
            )

    position_mode = str(mode_info.get("mode") or "trend")
    profit_pct = None
    if avg_cost is not None and float(avg_cost) > 0:
        profit_pct = close / float(avg_cost) - 1.0
    if position_mode == "swing":
        if close < ma10:
            return _signal(
                row,
                action="clear",
                ratio=1.0,
                reason=REASON_SWING_BREAK_MA10,
                rsi=rsi,
                extra={**mode_extra, "message": "波段模式跌破MA10，执行清仓"},
            )
        swing_trailing_profit = float(params.get("swing_trailing_profit_pct", 0.20))
        swing_trailing_dd = float(params.get("swing_trailing_drawdown_pct", 0.08))
        if (
            profit_pct is not None
            and profit_pct >= swing_trailing_profit
            and peak_close is not None
            and float(peak_close) > 0
            and close <= float(peak_close) * (1.0 - swing_trailing_dd)
        ):
            return _signal(
                row,
                action="reduce",
                ratio=0.5,
                reason=REASON_SWING_TRAILING_DD,
                rsi=rsi,
                extra={
                    **mode_extra,
                    "profit_pct": round(profit_pct, 6),
                    "peak_close": round(float(peak_close), 4),
                    "message": "波段模式盈利后从持仓高点回撤达到阈值，减半保护利润",
                },
            )
        swing_profit_ma5 = float(params.get("swing_profit_ma5_pct", 0.15))
        if profit_pct is not None and profit_pct >= swing_profit_ma5 and close < ma5:
            return _signal(
                row,
                action="reduce",
                ratio=0.5,
                reason=REASON_SWING_PROFIT_MA5,
                rsi=rsi,
                extra={**mode_extra, "profit_pct": round(profit_pct, 6), "message": "波段模式盈利后跌破MA5，减半锁定利润"},
            )
        swing_rsi_hot = float(params.get("swing_rsi_hot", 80))
        bearish_volume = (
            open_px is not None
            and volume is not None
            and volume_ma20 is not None
            and close < open_px
            and volume > volume_ma20
        )
        if rsi is not None and rsi > swing_rsi_hot and bearish_volume:
            return _signal(
                row,
                action="reduce",
                ratio=0.5,
                reason=REASON_SWING_RSI_BEAR_VOLUME,
                rsi=rsi,
                extra={**mode_extra, "message": "波段模式RSI极热且放量阴线，减半降风险"},
            )

    if close < ma10:
        return _signal(
            row,
            action="watch",
            ratio=0.0,
            reason=REASON_REDUCE_MA10,
            rsi=rsi,
            extra={**mode_extra, "message": "MA10减仓信号，仅提示风险，不执行减仓"},
        )

    if rsi is not None and rsi > exit_rsi and close > ma5 and not bool(state.get("rsi_reduce_done")):
        return _signal(row, action="watch", ratio=0.0, reason=REASON_REDUCE_RSI, rsi=rsi, extra=mode_extra)

    if close < ma5:
        if rsi is not None and rsi > exit_rsi:
            return None
        return _signal(
            row,
            action="watch",
            ratio=0.0,
            reason=REASON_REDUCE_MA5,
            rsi=rsi,
            extra={**mode_extra, "message": "MA5减仓信号，仅提示风险，不执行减仓"},
        )

    return None


def _signal(
    row: pd.Series,
    *,
    action: str,
    ratio: float,
    reason: str,
    rsi: float | None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ts = row["time"]
    date_s = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
    pct_label = (
        "清仓"
        if action == "clear"
        else (
            "减仓风险提示"
            if reason in {REASON_EARLY_HOLD_REDUCE_RISK, REASON_REDUCE_MA10, REASON_REDUCE_MA5}
            else ("可选卖出部分" if action == "watch" else _ratio_label(ratio))
        )
    )
    out = {
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
    if extra:
        out.update(extra)
    return out


def _mode_extra(mode_info: dict[str, Any]) -> dict[str, Any]:
    return {
        "position_mode": mode_info.get("mode"),
        "mode_reason": mode_info.get("reason"),
        "mode_evidence": mode_info.get("evidence"),
    }


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except Exception:
        return None


def _ratio_label(ratio: float) -> str:
    if abs(ratio - 1.0 / 3.0) < 1e-6:
        return "1/3"
    if abs(ratio - 0.5) < 1e-6:
        return "1/2"
    if abs(ratio - 1.0) < 1e-6:
        return "清仓"
    return f"{ratio:.2%}"
