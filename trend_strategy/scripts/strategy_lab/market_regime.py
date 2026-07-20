"""Market regime gate: index RSI floor before allowing new buy signals."""

from __future__ import annotations

from typing import Any

import pandas as pd

from .indicators import add_ma, add_rsi


def evaluate_index_bar(
    df: pd.DataFrame,
    bar_idx: int,
    *,
    ma_period: int,
    rsi_period: int,
    rsi_floor: float = 40.0,
) -> dict[str, Any] | None:
    if df is None or df.empty:
        return None
    if abs(bar_idx) > len(df):
        return None
    enriched = add_rsi(add_ma(df, [ma_period]), rsi_period)
    row = enriched.iloc[bar_idx]
    ma_key = f"ma_{ma_period}"
    rsi_key = f"rsi_{rsi_period}"
    if ma_key not in row.index or pd.isna(row[ma_key]):
        return None
    close = float(row["close"])
    ma_val = float(row[ma_key])
    rsi_val = float(row[rsi_key]) if rsi_key in row.index and pd.notna(row[rsi_key]) else None
    ts = row["time"]
    date_s = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
    return {
        "date": date_s,
        "close": round(close, 4),
        "ma": round(ma_val, 4),
        "rsi": None if rsi_val is None else round(rsi_val, 4),
        "above_ma": close > ma_val,
        "rsi_ok": rsi_val is None or rsi_val >= float(rsi_floor),
    }


def evaluate_market_regime(
    provider: Any,
    index_codes: dict[str, str],
    *,
    history_count: int = 60,
    ma_period: int = 20,
    rsi_period: int = 14,
    rsi_floor: float = 40.0,
    bar_idx: int = -1,
) -> dict[str, Any]:
    """Return regime snapshot for buy gating.

    allow_new_buys when no configured index RSI is below rsi_floor on the evaluation bar.
    Index MA (e.g. ma_20) is reported in indices.*.above_ma for reference only, not gating.
    """
    indices: dict[str, Any] = {}
    errors: list[str] = []

    for name, code in index_codes.items():
        try:
            df = provider.get_history(code, count=max(history_count, ma_period + rsi_period + 5))
            snap = evaluate_index_bar(
                df,
                bar_idx,
                ma_period=ma_period,
                rsi_period=rsi_period,
                rsi_floor=rsi_floor,
            )
            if snap is None:
                errors.append(f"{name}:short_history")
                indices[name] = {"code": code, "error": "short_history"}
                continue
            snap["code"] = code
            snap["rsi_ok"] = snap["rsi"] is None or float(snap["rsi"]) >= float(rsi_floor)
            indices[name] = snap
        except Exception as exc:
            errors.append(f"{name}:{exc}")
            indices[name] = {"code": code, "error": str(exc)[:200]}

    valid = [v for v in indices.values() if "error" not in v]
    all_rsi_ok = bool(valid) and all(bool(v.get("rsi_ok")) for v in valid)
    allow_new_buys = all_rsi_ok and not errors

    block_reasons: list[str] = []
    if not all_rsi_ok:
        block_reasons.append("index_rsi_below_floor")
    if errors:
        block_reasons.append("index_data_error")

    return {
        "allow_new_buys": allow_new_buys,
        "ma_period": ma_period,
        "rsi_period": rsi_period,
        "rsi_floor": rsi_floor,
        "bar_idx": bar_idx,
        "indices": indices,
        "block_reasons": block_reasons,
        "error": "; ".join(errors)[:500] if errors else None,
    }
