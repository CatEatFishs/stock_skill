#!/usr/bin/env python3
"""Scan all-market liquidity pool and emit buy/sell signals (no backtest, no order execution)."""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import pandas as pd

from simulated_trading_path import ensure_simulated_trading_on_path

ensure_simulated_trading_on_path()
from simulated_trading.market_data import MarketDataProvider
from strategy_lab.strategies import trend_pullback
from strategy_lab.position_reduce import empty_reduce_state, enrich_position_management, evaluate_reduce_signal
from strategy_lab.hot_sectors import (
    batch_stock_sectors,
    evaluate_hot_sector_entry_fit,
    evaluate_hot_sector_match,
    evaluate_style_filter,
    load_hot_sector_snapshot,
)
from strategy_lab.market_regime import evaluate_market_regime
from strategy_lab import strategy_params


def _import_simulated_engine(db_path: str | None):
    ensure_simulated_trading_on_path()
    from simulated_trading.engine import PaperTradingEngine
    from simulated_trading_runtime import get_default_db_path

    resolved = db_path or str(get_default_db_path())
    return PaperTradingEngine(resolved), resolved


def _parse_holdings(path: Path) -> list[str]:
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        digits = "".join(ch for ch in s if ch.isdigit())
        if len(digits) >= 6:
            out.append(digits[-6:].zfill(6))
    return out


def _normalize_code(code: str) -> str:
    digits = "".join(ch for ch in str(code) if ch.isdigit())
    if len(digits) >= 6:
        return digits[-6:].zfill(6)
    return str(code).strip()


def _merge_holdings(file_codes: list[str], paper_codes: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for code in file_codes + paper_codes:
        norm = _normalize_code(code)
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


def _holding_trading_days(df: pd.DataFrame, buy_date: str | None, current_time) -> int | None:
    if not buy_date:
        return None
    try:
        start = pd.to_datetime(buy_date).normalize()
        end = pd.to_datetime(current_time).normalize()
    except Exception:
        return None
    if end < start:
        return None
    dates = pd.to_datetime(df["time"], errors="coerce").dt.normalize()
    return int(((dates >= start) & (dates <= end)).sum())


def _peak_close_since(df: pd.DataFrame, buy_date: str | None, current_time) -> float | None:
    if not buy_date:
        return None
    try:
        start = pd.to_datetime(buy_date).normalize()
        end = pd.to_datetime(current_time).normalize()
    except Exception:
        return None
    if end < start or "close" not in df.columns:
        return None
    dates = pd.to_datetime(df["time"], errors="coerce").dt.normalize()
    window = df[(dates >= start) & (dates <= end)]
    if window.empty:
        return None
    peak = pd.to_numeric(window["close"], errors="coerce").max()
    if pd.isna(peak):
        return None
    return float(peak)


def _row_snapshot(enriched: pd.DataFrame, idx: int) -> dict | None:
    if idx < 0 or idx >= len(enriched):
        return None
    row = enriched.iloc[idx]
    rsi_period = int(strategy_params.TREND_PULLBACK_PARAMS.get("rsi_period", 14))
    rsi_key = f"rsi_{rsi_period}"
    ts = row["time"]
    date_s = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
    rsi_val = float(row[rsi_key]) if rsi_key in row.index and pd.notna(row[rsi_key]) else None
    return {
        "date": date_s,
        "close": round(float(row["close"]), 4),
        "score": round(float(row["score"]), 6),
        "rsi": None if rsi_val is None else round(rsi_val, 4),
        "macd_momentum": bool(row["macd_momentum"]) if "macd_momentum" in row.index and pd.notna(row["macd_momentum"]) else False,
        "entry": bool(row["entry"]),
        "exit": bool(row["exit"]),
    }


def _build_param_variants(base_params: dict, grid: dict) -> list[dict]:
    keys = [k for k, v in grid.items() if isinstance(v, list) and v]
    if not keys:
        return [dict(base_params)]
    values = [list(dict.fromkeys(grid[k])) for k in keys]
    variants: list[dict] = []
    for combo in itertools.product(*values):
        params = dict(base_params)
        for key, val in zip(keys, combo):
            params[key] = val
        variants.append(params)
    return variants


def _entry_consensus_ratio(df: pd.DataFrame, variants: list[dict], use_previous_day: bool) -> float:
    if df is None or df.empty or not variants:
        return 0.0
    idx = -2 if use_previous_day else -1
    if len(df) < 2 and use_previous_day:
        return 0.0
    votes = 0
    valid = 0
    for params in variants:
        try:
            enriched = trend_pullback(df, params)
            if enriched is None or enriched.empty:
                continue
            if abs(idx) > len(enriched):
                continue
            valid += 1
            if bool(enriched.iloc[idx].get("entry", False)):
                votes += 1
        except Exception:
            continue
    if valid == 0:
        return 0.0
    return votes / valid


def _edge_after_cost(signal_bar: dict, roundtrip_cost_bps: float) -> float:
    score = max(float(signal_bar.get("score", 0.0)), 0.0)
    cost = max(float(roundtrip_cost_bps), 0.0) / 10000.0
    return score - cost


def _passes_cost_filter(signal_bar: dict, roundtrip_cost_bps: float) -> bool:
    return _edge_after_cost(signal_bar, roundtrip_cost_bps) > 0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _rating_for_score(score: float) -> tuple[str, str, str]:
    if score >= 80:
        return "🔥 强买候选", "买入", "优先低吸"
    if score >= 70:
        return "🔥 买入候选", "买入", "回踩低吸"
    if score >= 60:
        return "👀 观察低吸", "观察", "等回踩确认"
    if score >= 50:
        return "📌 观察", "观察", "只看不追"
    if score >= 40:
        return "⚠️ 谨慎", "谨慎", "降低仓位或放弃"
    return "🛑 回避", "回避", "不买"


def _score_rsi(rsi: float | None) -> float:
    if rsi is None:
        return 0.0
    low = float(strategy_params.TREND_PULLBACK_PARAMS.get("bull_rsi_low", 42))
    high = float(strategy_params.TREND_PULLBACK_PARAMS.get("bull_rsi_high", 72))
    if rsi < low or rsi > high:
        return 0.0
    midpoint = 58.0
    half_range = max(midpoint - low, high - midpoint, 1.0)
    return _clamp(1.0 - abs(float(rsi) - midpoint) / half_range, 0.0, 1.0)


def _score_hot_sector(item: dict) -> float:
    score = 0.0
    if item.get("hot_sector_matched"):
        score += 5.0
    if item.get("hot_sector_entry_fit_passed"):
        score += 5.0
    if item.get("relative_to_sector_ok") or item.get("near_ma_fast"):
        score += 5.0
    return _clamp(score / 15.0, 0.0, 1.0)


def _score_buy_item(item: dict, *, use_previous_day: bool) -> dict:
    weights = strategy_params.SIGNAL_SCORE_WEIGHTS
    signal_bar = item.get("signal_bar") or {}
    asof_bar = item.get("asof_bar") or {}
    raw_score = max(float(signal_bar.get("score") or 0.0), 0.0)
    edge = max(float(item.get("edge_after_cost") or 0.0), 0.0)
    consensus = max(float(item.get("entry_consensus_ratio") or 0.0), 0.0)

    components = {
        "trend": _clamp(raw_score / 0.20, 0.0, 1.0) * weights["trend"],
        "rsi": _score_rsi(signal_bar.get("rsi")) * weights["rsi"],
        "robustness": _clamp(consensus, 0.0, 1.0) * weights["robustness"],
        "edge_after_cost": _clamp(edge / 0.12, 0.0, 1.0) * weights["edge_after_cost"],
        "hot_sector": _score_hot_sector(item) * weights["hot_sector"],
        "style": (1.0 if item.get("style_filter_passed", True) else 0.0) * weights["style"],
        "market_regime": (1.0 if item.get("market_regime_passed", True) else 0.0) * weights["market_regime"],
        "macd_momentum": (1.0 if signal_bar.get("macd_momentum") else 0.0) * weights["macd_momentum"],
    }
    total = sum(components.values())

    risk_flags: list[str] = []
    if item.get("already_held"):
        risk_flags.append("已有持仓不加仓")
        total = min(total, 49.0)
    if not item.get("cost_filter_passed", True):
        risk_flags.append("成本空间不足")
        total = min(total, 49.0)
    if not item.get("consensus_filter_passed", True):
        risk_flags.append("鲁棒性不足")
        total = min(total, 49.0)
    if not item.get("style_filter_passed", True):
        risk_flags.append(item.get("style_reject_reason") or "风格过滤未通过")
        total = min(total, 39.0)
    if not item.get("hot_sector_filter_passed", True):
        risk_flags.append("热门板块过滤未通过")
        total = min(total, 49.0)
    if not item.get("market_regime_passed", True):
        risk_flags.append("大盘环境暂停新开仓")
        total = min(total, 49.0)
    if use_previous_day and asof_bar:
        if asof_bar.get("exit"):
            risk_flags.append("今日已触发exit")
            total = min(total, 29.0)
        elif not asof_bar.get("entry"):
            risk_flags.append("今日不再满足entry")
            total = min(total, 59.0)

    rating_label, long_term_action, short_term_action = _rating_for_score(total)
    item["stock_skill_score"] = round(float(total), 1)
    item["rating_label"] = rating_label
    item["long_term_action"] = long_term_action
    item["short_term_action"] = short_term_action
    item["score_components"] = {k: round(float(v), 2) for k, v in components.items()}
    item["risk_flags"] = risk_flags
    return item


def _score_buy_items(items: list[dict], *, use_previous_day: bool) -> None:
    for item in items:
        _score_buy_item(item, use_previous_day=use_previous_day)


def _hot_sector_filter_active(hot_snapshot: dict, disabled: bool) -> bool:
    if disabled:
        return False
    has_boards = bool(hot_snapshot.get("industry_names") or hot_snapshot.get("concept_names"))
    return has_boards


def _apply_hot_sector_to_buy_items(
    items: list[dict],
    hot_snapshot: dict,
    sector_map: dict[str, dict],
    history_map: dict[str, pd.DataFrame],
    *,
    filter_active: bool,
    bar_idx: int,
    entry_params: dict,
) -> None:
    for item in items:
        code = _normalize_code(item.get("code", ""))
        stock_sector = sector_map.get(code)
        match_info = evaluate_hot_sector_match(stock_sector, hot_snapshot)
        fit_info = evaluate_hot_sector_entry_fit(
            history_map.get(code),
            bar_idx,
            match_info,
            entry_params,
        )
        style_info = evaluate_style_filter(
            stock_sector,
            industry_blacklist=strategy_params.INDUSTRY_BLACKLIST,
            min_concepts=strategy_params.REQUIRE_MIN_STOCK_CONCEPTS,
        )
        item.update(match_info)
        item.update(fit_info)
        item.update(style_info)
        if filter_active:
            item["hot_sector_filter_passed"] = bool(match_info.get("hot_sector_matched")) and bool(
                fit_info.get("hot_sector_entry_fit_passed")
            )
        else:
            item["hot_sector_filter_passed"] = True


def _collect_entry_codes(*lists: list[dict]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for bucket in lists:
        for item in bucket:
            code = _normalize_code(item.get("code", ""))
            if code and code not in seen:
                seen.add(code)
                out.append(code)
    return out


def _scan_one(
    provider: MarketDataProvider,
    code: str,
    history_count: int,
) -> tuple[str, pd.DataFrame | None, pd.DataFrame | None, str | None]:
    try:
        df = provider.get_history(code, count=history_count)
        if df is None or len(df) < max(
            int(strategy_params.TREND_PULLBACK_PARAMS.get("slow", 20)) + 3,
            30,
        ):
            return code, df, None, "short_history"
        out = trend_pullback(df, strategy_params.TREND_PULLBACK_PARAMS)
        return code, df, out, None
    except Exception as exc:
        return code, None, None, str(exc)[:200]


def _fmt_num(value, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_pct(value) -> str:
    if value is None:
        return "N/A"
    try:
        return f"{float(value):+.2f}%"
    except (TypeError, ValueError):
        return str(value)


def _fmt_change_from_bars(item: dict) -> str:
    signal_bar = item.get("signal_bar") or {}
    asof_bar = item.get("asof_bar") or {}
    sig_close = signal_bar.get("close")
    asof_close = asof_bar.get("close")
    if sig_close in (None, 0) or asof_close is None:
        return "涨跌N/A"
    try:
        pct = (float(asof_close) / float(sig_close) - 1.0) * 100.0
        return _fmt_pct(pct)
    except (TypeError, ValueError, ZeroDivisionError):
        return "涨跌N/A"


def _index_label(key: str) -> str:
    return {
        "hs300": "沪深300",
        "zz1000": "中证1000",
    }.get(key, key)


def _format_regime_line(payload: dict) -> str:
    regime = (payload.get("market_regime") or {}).get("from_last_close") or {}
    indices = regime.get("indices") or {}
    parts = []
    for key in ("hs300", "zz1000"):
        idx = indices.get(key)
        if not idx:
            continue
        ma_state = "MA20上方" if idx.get("above_ma") else "MA20下方"
        rsi_state = "RSI达标" if idx.get("rsi_ok") else "RSI偏弱"
        parts.append(
            f"{_index_label(key)}·{_fmt_num(idx.get('close'), 2, '点')}·RSI{_fmt_num(idx.get('rsi'), 1)}·{ma_state}/{rsi_state}"
        )
    allow = bool(regime.get("allow_new_buys"))
    gate = "收盘新开仓：允许" if allow else "收盘新开仓：暂停"
    if regime.get("block_reasons"):
        gate += f"（{','.join(regime.get('block_reasons') or [])}）"
    if not parts:
        return f"**大盘** {gate}"
    return f"**大盘** {'；'.join(parts)}。{gate}"


def _format_hot_line(payload: dict) -> str:
    hot = payload.get("hot_sectors") or {}
    industries = hot.get("industry_top") or []
    concepts = hot.get("concept_top") or []

    def top_names(rows: list[dict], n: int = 3) -> str:
        out = []
        for row in rows[:n]:
            name = row.get("groupLabel") or row.get("groupKey") or ""
            pct = _fmt_pct(row.get("changePct"))
            if name:
                out.append(f"{name}{pct}")
        return "、".join(out) if out else "N/A"

    return f"**热点** 行业：{top_names(industries)}；概念：{top_names(concepts)}"


def _format_regime_table(payload: dict) -> list[str]:
    regime = (payload.get("market_regime") or {}).get("from_last_close") or {}
    indices = regime.get("indices") or {}
    floor = regime.get("rsi_floor", 35)
    mode = regime.get("rsi_mode", "any")
    mode_label = "任一过线即可" if mode == "any" else "须全部过线"
    lines = [
        f"**大盘新开仓开关**（{mode_label}，RSI≥{_fmt_num(floor, 0)}）",
        "",
        "| 指数 | RSI | 策略要求 | 结论 |",
        "|---|---:|---:|---|",
    ]
    for key in ("hs300", "zz1000"):
        idx = indices.get(key) or {}
        rsi = _fmt_num(idx.get("rsi"), 2)
        verdict = "✅ 达标" if idx.get("rsi_ok") else "❌ 不达标"
        lines.append(f"| {_index_label(key)} | {rsi} | ≥ {_fmt_num(floor, 0)} | {verdict} |")
    overall = "✅ 允许新开仓" if regime.get("allow_new_buys") else "❌ 暂停新开仓"
    lines.append("")
    lines.append(f"综合：{overall}")
    return lines


def _quote_for_item(item: dict) -> dict:
    quote = item.get("quote") or {}
    if isinstance(quote, dict):
        return quote
    return {}


def _primary_theme(item: dict) -> str:
    industry = item.get("matched_hot_industry") or item.get("stock_industry")
    concepts = item.get("matched_hot_concepts") or []
    if industry:
        return str(industry).replace("(A股)", "")
    if concepts:
        text = str(concepts[0])
        return text.replace("概念", "")
    return "形态"


def _direction_for_item(item: dict) -> str:
    quote = _quote_for_item(item)
    pct = quote.get("change_pct")
    theme = _primary_theme(item)
    try:
        pct_f = float(pct)
    except (TypeError, ValueError):
        pct_f = 0.0
    if pct_f >= 7:
        return f"{theme}，涨幅偏高不追"
    if pct_f >= 3:
        return f"{theme}观察，不追"
    if item.get("short_term_action") in {"优先低吸", "回踩低吸", "等回踩确认"}:
        return str(item.get("short_term_action"))
    return f"{theme}观察"


def _attach_realtime_quotes(items: list[dict], provider: MarketDataProvider | None) -> None:
    if not items or provider is None:
        return
    codes: list[str] = []
    seen: set[str] = set()
    for item in items:
        code = _normalize_code(item.get("code", ""))
        if code and code not in seen:
            seen.add(code)
            codes.append(code)

    quotes: dict[str, dict] = {}

    def fetch(code: str) -> tuple[str, dict]:
        q = provider.get_quote(code)
        return code, asdict(q)

    workers = min(8, max(1, len(codes)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(fetch, code): code for code in codes}
        for future in as_completed(futures):
            code = futures[future]
            try:
                c, quote = future.result()
                quotes[_normalize_code(c)] = quote
            except Exception as exc:
                quotes[code] = {"error": str(exc)[:120]}

    for item in items:
        code = _normalize_code(item.get("code", ""))
        quote = quotes.get(code)
        if quote:
            item["quote"] = quote
            if not item.get("name") and quote.get("name"):
                item["name"] = quote.get("name")


def _format_candidate_table(title: str, items: list[dict]) -> list[str]:
    lines = [
        title,
        "",
        "| 代码 | 标的 | 现价 | 涨跌幅 | 评分 | 评级 | 方向 |",
        "|---|---|---:|---:|---:|---|---|",
    ]
    if not items:
        lines.append("| - | 无 | - | - | - | - | - |")
        return lines

    for item in items:
        code = item.get("code", "")
        quote = _quote_for_item(item)
        name = quote.get("name") or item.get("name") or code
        price = _fmt_num(quote.get("price"), 2)
        change_pct = _fmt_pct(quote.get("change_pct"))
        score = _fmt_num(item.get("stock_skill_score"), 1)
        rating = item.get("rating_label") or "📌 观察"
        direction = _direction_for_item(item)
        lines.append(f"| {code} | {name} | {price} | {change_pct} | {score} | {rating} | {direction} |")
    return lines


def _format_buy_item(item: dict, *, stale_signal: bool = False) -> str:
    code = item.get("code", "")
    name = item.get("name") or code
    signal_bar = item.get("signal_bar") or {}
    asof_bar = item.get("asof_bar") or {}
    close = asof_bar.get("close", signal_bar.get("close"))
    change = _fmt_change_from_bars(item)
    score = _fmt_num(item.get("stock_skill_score"), 1)
    rating = item.get("rating_label") or "📌 观察"
    industry = item.get("stock_industry") or "未识别"
    concepts = item.get("matched_hot_concepts") or []
    concept_text = "、".join(concepts[:4]) if concepts else (item.get("matched_hot_industry") or "未命中")
    ret_text = _fmt_pct(item.get("stock_ret_nd_pct"))
    fit_reason = item.get("fit_reason") or "trend_pullback"
    risk_flags = item.get("risk_flags") or []

    title_icon = "⚠️" if stale_signal else "🔥"
    lines = [
        f"**{title_icon} {name}({code})** {score}分·{change}·{rating}",
        f"  长线：{item.get('long_term_action', '观察')} - {industry} / {concept_text}",
        f"  短线：{item.get('short_term_action', '观察')} - 近{strategy_params.HOT_SECTOR_PULLBACK_RET_DAYS}日{ret_text}，{fit_reason}",
    ]
    if asof_bar:
        if asof_bar.get("exit"):
            lines.append("  操作：今日已触发 exit，不作为买入候选")
        elif asof_bar.get("entry"):
            lines.append("  操作：形态仍有效，按回踩低吸处理，不追高")
        else:
            lines.append("  操作：昨日信号仍可观察，但今日不再满足 entry，等下一次回踩确认")
    else:
        lines.append("  操作：仅按信号日判断，执行前需复核实时价")
    if risk_flags:
        lines.append(f"  风险：{'；'.join(risk_flags[:3])}")
    return "\n".join(lines)


def _format_reduce_item(item: dict) -> str:
    code = item.get("code", "")
    name = item.get("name") or code
    if "error" in item:
        return f"**⚠️ {name}({code})** 持仓扫描失败\n  风险：{item.get('error')}"
    signal_bar = item.get("signal_bar") or {}
    action = item.get("action") or "reduce"
    pct = item.get("reduce_pct_label") or ""
    reason = item.get("message") or item.get("reason") or ""
    close = _fmt_num(signal_bar.get("close"), 2)
    rsi = _fmt_num(signal_bar.get("rsi"), 1)
    if action == "clear":
        action_label = "清仓"
        long_line = f"  长线：持仓管理 - 触发{action_label}"
    elif action == "watch":
        action_label = pct or "过热提示"
        long_line = f"  长线：持仓管理 - {action_label}"
    elif action == "skip":
        long_line = f"  长线：持仓管理 - {item.get('message') or '跳过'}"
    else:
        action_label = f"减仓{pct}"
        long_line = f"  长线：持仓管理 - 触发{action_label}"
    return "\n".join(
        [
            f"**⚠️ {name}({code})** {close}",
            long_line,
            f"  短线：{reason}，RSI{rsi}",
        ]
    )


def _print_human_report(payload: dict, *, holdings: list[str], provider: MarketDataProvider | None = None) -> None:
    buy = payload.get("buy") or {}
    buy_prev = buy.get("from_previous_day_close") or []
    buy_last = buy.get("from_last_close") or []
    raw_prev = buy.get("from_previous_day_close_raw") or []
    raw_last = buy.get("from_last_close_raw") or []
    buy_prev_total = int(buy.get("from_previous_day_close_total") or len(buy_prev))
    buy_last_total = int(buy.get("from_last_close_total") or len(buy_last))
    reduce_signals = payload.get("reduce") or []
    sell_signals = payload.get("sell") or []
    latest_bar_date = payload.get("latest_bar_date") or "N/A"
    display_cap = int(payload.get("max_buy_candidates") or 8)
    display_cap = max(8, display_cap)

    formal_items = buy_last[:display_cap]
    stale_items = buy_prev[:display_cap]
    observation_items = raw_last[:display_cap] if not formal_items else []
    quote_items = formal_items + stale_items + observation_items
    _attach_realtime_quotes(quote_items, provider)

    print(f"📈 stock_skill · 趋势回踩复盘 · {latest_bar_date}")
    for line in _format_regime_table(payload):
        print(line)
    print()
    regime = (payload.get("market_regime") or {}).get("from_last_close") or {}
    allow = bool(regime.get("allow_new_buys"))
    if allow:
        print("**结论：大盘开关已放行，可只看正式买入候选。**")
    else:
        reasons = ",".join(regime.get("block_reasons") or []) or "market_regime_blocked"
        print(f"**结论：今天不新开仓，不买。** 拦截原因：`{reasons}`")
    print()
    print(_format_hot_line(payload))
    print(
        f"**扫描** 成交额池{payload.get('universe_size')}只 · "
        f"昨日信号{buy_prev_total}只/展示{len(buy_prev)}只 · "
        f"收盘信号{buy_last_total}只/展示{len(buy_last)}只 · "
        f"过滤前收盘形态{len(raw_last)}只 · "
        f"错误{payload.get('errors_total', 0)}只"
    )
    print()

    if formal_items:
        for line in _format_candidate_table("**🔥 收盘买入候选**", formal_items):
            print(line)
        print()
    else:
        print("**🔥 收盘买入候选**")
        print("无。收盘信号为空或被大盘环境/热点/成本/鲁棒性过滤拦截。")
        print()

    if observation_items:
        for line in _format_candidate_table("**👀 过滤前观察名单，不作为买入清单**", observation_items):
            print(line)
        print()

    if stale_items:
        for line in _format_candidate_table("**👀 昨日信号今日观察**", stale_items):
            print(line)
        print()
    else:
        print("**👀 昨日信号今日观察**")
        print("无。")
        print()

    print("**⚠️ 持仓风控**")
    if holdings:
        actionable = reduce_signals or sell_signals
        if actionable:
            for item in reduce_signals:
                print(_format_reduce_item(item))
                print()
            reduce_codes = {item.get("code") for item in reduce_signals}
            for item in sell_signals:
                if item.get("code") not in reduce_codes:
                    print(_format_reduce_item({**item, "action": "clear", "reason": item.get("via", "exit")}))
                    print()
        else:
            print("未触发减仓/清仓。")
    else:
        print("未传 --holdings / --paper-account，未扫描持仓减仓和清仓。")
    print()

    print(
        f"⚠️ 仅供策略复盘参考，不自动下单。回复或运行 `python3 trend_strategy/scripts/daily_decisions.py --json` 查看完整结构化数据。"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="All-market pool + trend_pullback: buy/sell signals for decision support",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=strategy_params.UNIVERSE_TOP_N_DEFAULT,
        help="All-market universe size by turnover",
    )
    parser.add_argument(
        "--history-count",
        type=int,
        default=120,
        help="Daily bars to fetch per symbol",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=10,
        help="Parallel workers for history fetch",
    )
    parser.add_argument(
        "--holdings",
        type=Path,
        default=None,
        help="Optional file: one stock code per line, check sell/reduce signals",
    )
    parser.add_argument(
        "--paper-account",
        default=None,
        help="Load holdings from paper trading account and persist reduce state in its SQLite DB",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Paper trading SQLite path (default: user data dir paper_trading.db)",
    )
    parser.add_argument(
        "--no-apply-reduce-state",
        action="store_true",
        help="Preview reduce signals without writing position_strategy_state",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print one JSON object to stdout",
    )
    parser.add_argument(
        "--max-buys",
        type=int,
        default=strategy_params.MAX_BUY_CANDIDATES,
        help="Cap buy lists after score sort; 0 means no cap",
    )
    parser.add_argument(
        "--roundtrip-cost-bps",
        type=float,
        default=strategy_params.DEFAULT_ROUNDTRIP_COST_BPS,
        help="Estimated roundtrip cost in bps for cost-aware filtering",
    )
    parser.add_argument(
        "--entry-consensus-min",
        type=float,
        default=strategy_params.ENTRY_CONSENSUS_MIN_DEFAULT,
        help="Minimum consensus ratio from robustness grid",
    )
    parser.add_argument(
        "--disable-robust-check",
        action="store_true",
        help="Disable entry robustness check against parameter grid",
    )
    parser.add_argument(
        "--disable-hot-sector-check",
        action="store_true",
        help="Disable hot sector (top-N industry/concept) entry filter",
    )
    parser.add_argument(
        "--hot-sector-top-n",
        type=int,
        default=strategy_params.HOT_SECTOR_TOP_N_DEFAULT,
        help="Hot industry/concept board highlight count by weekly change (default 10)",
    )
    parser.add_argument(
        "--hot-sector-pool-n",
        type=int,
        default=strategy_params.HOT_SECTOR_MATCH_POOL_N_DEFAULT,
        help="Industry/concept match pool size by weekly change (default 50, either may match)",
    )
    parser.add_argument(
        "--disable-market-regime-check",
        action="store_true",
        help="Disable index regime gate (HS300/ZZ1000 RSI floor, any-one mode)",
    )
    args = parser.parse_args()

    holdings_file = _parse_holdings(args.holdings) if args.holdings else []
    paper_engine = None
    paper_db_path = None
    paper_codes: list[str] = []
    paper_positions_by_code: dict[str, dict] = {}
    if args.paper_account:
        try:
            paper_engine, paper_db_path = _import_simulated_engine(args.db_path)
            positions = paper_engine.get_positions(args.paper_account)
            paper_positions_by_code = {
                _normalize_code(item["symbol"]): item for item in positions if int(item.get("qty") or 0) > 0
            }
            paper_codes = list(paper_positions_by_code.keys())
        except Exception as exc:
            print(f"ERROR: paper account load failed: {exc}", file=sys.stderr)
            sys.exit(1)

    holdings_set = set(_merge_holdings(holdings_file, paper_codes))

    provider = MarketDataProvider()
    universe = provider.get_all_market_universe(as_of=None, top_n=int(args.top_n))
    if not universe:
        print("ERROR: empty universe", file=sys.stderr)
        sys.exit(1)

    rows: list[tuple[str, pd.DataFrame | None, pd.DataFrame | None, str | None]] = []
    with ThreadPoolExecutor(max_workers=max(1, int(args.workers))) as pool:
        futs = {pool.submit(_scan_one, provider, c, int(args.history_count)): c for c in universe}
        for fut in as_completed(futs):
            rows.append(fut.result())

    param_variants = _build_param_variants(
        strategy_params.TREND_PULLBACK_PARAMS,
        strategy_params.ROBUSTNESS_PARAM_GRID,
    )

    buy_prev_raw: list[dict] = []
    buy_last_raw: list[dict] = []
    buy_prev: list[dict] = []
    buy_last: list[dict] = []
    errors: list[dict] = []
    history_map: dict[str, pd.DataFrame] = {}

    for code, history_df, enriched, err in rows:
        if err:
            errors.append({"code": code, "error": err})
            continue
        assert enriched is not None
        if history_df is not None:
            history_map[code] = history_df
        last = _row_snapshot(enriched, len(enriched) - 1)
        prev = _row_snapshot(enriched, len(enriched) - 2) if len(enriched) >= 2 else None
        if prev and prev.get("entry"):
            consensus_ratio = (
                _entry_consensus_ratio(history_df, param_variants, use_previous_day=True)
                if (history_df is not None and not args.disable_robust_check)
                else 1.0
            )
            edge_after_cost = _edge_after_cost(prev, float(args.roundtrip_cost_bps))
            item = {
                "code": code,
                "signal_bar": prev,
                "asof_bar": last,
                "entry_consensus_ratio": round(consensus_ratio, 4),
                "edge_after_cost": round(edge_after_cost, 6),
                "cost_filter_passed": edge_after_cost > 0,
                "consensus_filter_passed": consensus_ratio >= float(args.entry_consensus_min),
                "already_held": code in holdings_set,
            }
            buy_prev_raw.append(item)
        if last and last.get("entry"):
            consensus_ratio = (
                _entry_consensus_ratio(history_df, param_variants, use_previous_day=False)
                if (history_df is not None and not args.disable_robust_check)
                else 1.0
            )
            edge_after_cost = _edge_after_cost(last, float(args.roundtrip_cost_bps))
            item = {
                "code": code,
                "signal_bar": last,
                "entry_consensus_ratio": round(consensus_ratio, 4),
                "edge_after_cost": round(edge_after_cost, 6),
                "cost_filter_passed": edge_after_cost > 0,
                "consensus_filter_passed": consensus_ratio >= float(args.entry_consensus_min),
                "already_held": code in holdings_set,
            }
            buy_last_raw.append(item)

    hot_snapshot = load_hot_sector_snapshot(
        top_n=int(args.hot_sector_top_n),
        match_pool_n=int(args.hot_sector_pool_n),
        sort=strategy_params.HOT_SECTOR_SORT_DEFAULT,
    )
    hot_filter_active = _hot_sector_filter_active(hot_snapshot, bool(args.disable_hot_sector_check))
    entry_params = dict(strategy_params.TREND_PULLBACK_PARAMS)
    entry_params["hot_sector_pullback_ret_days"] = strategy_params.HOT_SECTOR_PULLBACK_RET_DAYS
    entry_params["hot_pullback_ceiling"] = strategy_params.HOT_SECTOR_TIGHT_PULLBACK_CEILING

    market_regime_prev = evaluate_market_regime(
        provider,
        strategy_params.MARKET_REGIME_INDEX_CODES,
        history_count=int(args.history_count),
        ma_period=strategy_params.MARKET_REGIME_MA_PERIOD,
        rsi_period=strategy_params.MARKET_REGIME_RSI_PERIOD,
        rsi_floor=strategy_params.MARKET_REGIME_RSI_FLOOR,
        rsi_mode=strategy_params.MARKET_REGIME_RSI_MODE,
        bar_idx=-2,
    )
    market_regime_last = evaluate_market_regime(
        provider,
        strategy_params.MARKET_REGIME_INDEX_CODES,
        history_count=int(args.history_count),
        ma_period=strategy_params.MARKET_REGIME_MA_PERIOD,
        rsi_period=strategy_params.MARKET_REGIME_RSI_PERIOD,
        rsi_floor=strategy_params.MARKET_REGIME_RSI_FLOOR,
        rsi_mode=strategy_params.MARKET_REGIME_RSI_MODE,
        bar_idx=-1,
    )
    market_regime_check_active = not bool(args.disable_market_regime_check)

    entry_codes = _collect_entry_codes(buy_prev_raw, buy_last_raw)
    sector_map = batch_stock_sectors(entry_codes, workers=max(1, int(args.workers)))
    _apply_hot_sector_to_buy_items(
        buy_prev_raw,
        hot_snapshot,
        sector_map,
        history_map,
        filter_active=hot_filter_active,
        bar_idx=-2,
        entry_params=entry_params,
    )
    _apply_hot_sector_to_buy_items(
        buy_last_raw,
        hot_snapshot,
        sector_map,
        history_map,
        filter_active=hot_filter_active,
        bar_idx=-1,
        entry_params=entry_params,
    )

    for item in buy_prev_raw:
        item["market_regime_passed"] = (
            bool(market_regime_prev.get("allow_new_buys")) if market_regime_check_active else True
        )
    for item in buy_last_raw:
        item["market_regime_passed"] = (
            bool(market_regime_last.get("allow_new_buys")) if market_regime_check_active else True
        )

    _score_buy_items(buy_prev_raw, use_previous_day=True)
    _score_buy_items(buy_last_raw, use_previous_day=False)

    def _passes_buy_filters(item: dict, *, use_previous_day: bool) -> bool:
        ok = bool(item.get("cost_filter_passed")) and bool(item.get("consensus_filter_passed"))
        ok = ok and bool(item.get("style_filter_passed", True))
        if hot_filter_active:
            ok = ok and bool(item.get("hot_sector_filter_passed"))
        if market_regime_check_active:
            regime = market_regime_prev if use_previous_day else market_regime_last
            ok = ok and bool(regime.get("allow_new_buys"))
        if strategy_params.BUY_POLICY_NO_ADD_TO_HOLDINGS:
            ok = ok and not bool(item.get("already_held"))
        return ok

    buy_prev = [x for x in buy_prev_raw if _passes_buy_filters(x, use_previous_day=True)]
    buy_last = [x for x in buy_last_raw if _passes_buy_filters(x, use_previous_day=False)]

    buy_prev.sort(key=lambda x: (float(x.get("stock_skill_score") or 0.0), float(x["signal_bar"]["score"])), reverse=True)
    buy_last.sort(key=lambda x: (float(x.get("stock_skill_score") or 0.0), float(x["signal_bar"]["score"])), reverse=True)

    cap = int(args.max_buys)
    if cap > 0:
        buy_prev_out = buy_prev[:cap]
        buy_last_out = buy_last[:cap]
    else:
        buy_prev_out = buy_prev
        buy_last_out = buy_last

    holdings = list(holdings_set)
    reduce_params = dict(strategy_params.TREND_PULLBACK_PARAMS)
    apply_reduce_state = bool(args.paper_account) and not bool(args.no_apply_reduce_state)

    sell_signals: list[dict] = []
    reduce_signals: list[dict] = []
    for code in holdings:
        paper_position = paper_positions_by_code.get(code)
        position_state = empty_reduce_state()
        if paper_engine is not None and args.paper_account:
            try:
                stored = paper_engine.get_position_strategy_state(
                    args.paper_account,
                    code,
                    strategy_params.STRATEGY_NAME,
                )
                position_state = {
                    "rsi_reduce_done": bool(stored.get("rsi_reduce_done")),
                    "ma5_reduce_done": bool(stored.get("ma5_reduce_done")),
                    "ma10_reduce_done": bool(stored.get("ma10_reduce_done")),
                }
            except Exception as exc:
                reduce_signals.append({"code": code, "error": f"state_load: {exc}"[:200]})
                continue

        try:
            df = provider.get_history(code, count=int(args.history_count))
            min_bars = max(30, max(strategy_params.TREND_PULLBACK_PARAMS.get("slow", 20), 20) + 3)
            if df is None or len(df) < min_bars:
                reduce_signals.append({"code": code, "error": "short_history"})
                continue
            mgmt = enrich_position_management(df, reduce_params)
            row = mgmt.iloc[-1]
            holding_trading_days = None
            avg_cost = None
            peak_close = None
            if paper_position:
                buy_date = paper_position.get("latest_acquired_date") or paper_position.get("first_acquired_date")
                holding_trading_days = _holding_trading_days(
                    mgmt,
                    buy_date,
                    row["time"],
                )
                peak_close = _peak_close_since(mgmt, buy_date, row["time"])
                avg_cost = float(paper_position.get("avg_cost") or 0.0)
            reduce_sig = evaluate_reduce_signal(
                row,
                position_state,
                reduce_params,
                holding_trading_days=holding_trading_days,
                avg_cost=avg_cost,
                peak_close=peak_close,
            )
            exit_enriched = trend_pullback(df, strategy_params.TREND_PULLBACK_PARAMS)
            last = _row_snapshot(exit_enriched, len(exit_enriched) - 1)
        except Exception as exc:
            reduce_signals.append({"code": code, "error": str(exc)[:200]})
            continue

        nm = ""
        try:
            nm = provider.get_quote(code).name or ""
        except Exception:
            pass

        if reduce_sig:
            item = {
                "code": code,
                "name": nm,
                "state_before": dict(position_state),
                **reduce_sig,
            }
            if reduce_sig.get("state_flag") and apply_reduce_state and paper_engine is not None and args.paper_account:
                try:
                    item["state_after"] = paper_engine.mark_position_strategy_reduce(
                        args.paper_account,
                        code,
                        strategy_params.STRATEGY_NAME,
                        reduce_sig["reason"],
                    )
                    item["state_applied"] = True
                except Exception as exc:
                    item["state_applied"] = False
                    item["state_error"] = str(exc)[:200]
            else:
                item["state_applied"] = False
            reduce_signals.append(item)
            if reduce_sig["action"] == "clear" and last:
                sell_signals.append({"code": code, "name": nm, "signal_bar": last, "via": "reduce_clear"})
        elif last and last.get("exit"):
            sell_signals.append({"code": code, "name": nm, "signal_bar": last, "via": "exit"})

    names: dict[str, str] = {}
    for bucket in (buy_prev_out, buy_last_out):
        for item in bucket:
            c = item["code"]
            if c not in names:
                try:
                    names[c] = provider.get_quote(c).name or ""
                except Exception:
                    names[c] = ""
            item["name"] = names[c]

    latest_bar_date = None
    for _, _, enriched, err in rows:
        if err or enriched is None or enriched.empty:
            continue
        t = enriched.iloc[-1]["time"]
        latest_bar_date = t.strftime("%Y-%m-%d") if hasattr(t, "strftime") else str(t)[:10]
        break

    payload = {
        "strategy": strategy_params.STRATEGY_NAME,
        "latest_bar_date": latest_bar_date,
        "universe_size": len(universe),
        "max_buy_candidates": cap if cap > 0 else None,
        "roundtrip_cost_bps": float(args.roundtrip_cost_bps),
        "entry_consensus_min": float(args.entry_consensus_min),
        "robust_check_enabled": not bool(args.disable_robust_check),
        "hot_sector_check_enabled": hot_filter_active,
        "hot_sector_top_n": int(args.hot_sector_top_n),
        "hot_sector_pool_n": int(args.hot_sector_pool_n),
        "hot_sectors": hot_snapshot,
        "market_regime_check_enabled": market_regime_check_active,
        "market_regime": {
            "from_previous_day_close": market_regime_prev,
            "from_last_close": market_regime_last,
        },
        "buy_policy": {
            "no_add_to_existing_holdings": strategy_params.BUY_POLICY_NO_ADD_TO_HOLDINGS,
            "note": "已有持仓不出买入信号，仅输出减仓/清仓",
        },
        "style_filter": {
            "industry_blacklist": strategy_params.INDUSTRY_BLACKLIST,
            "require_min_stock_concepts": strategy_params.REQUIRE_MIN_STOCK_CONCEPTS,
            "note": "行业命中黑名单或概念标签不足则剔除",
        },
        "scoring_policy": {
            "name": "stock_skill_score",
            "scale": "0-100",
            "note": "参考 BigA 展示方式，但仅评价 trend_pullback 策略信号强弱，不包含基本面催化总分",
            "weights": strategy_params.SIGNAL_SCORE_WEIGHTS,
            "rating_thresholds": [
                {"min": 80, "label": "🔥 强买候选"},
                {"min": 70, "label": "🔥 买入候选"},
                {"min": 60, "label": "👀 观察低吸"},
                {"min": 50, "label": "📌 观察"},
                {"min": 40, "label": "⚠️ 谨慎"},
                {"min": 0, "label": "🛑 回避"},
            ],
        },
        "reduce_policy": {
            "basis": "remaining_position",
            "mode": "dual: trend/swing",
            "note": "趋势模式MA5/MA10只提示风险；波段模式会对破MA10、盈利后破MA5、盈利后高点回撤和极热放量阴线执行减仓/清仓",
            "early_hold_protection": "买入后前3个交易日触发ma_5/ma_10时只提示减仓风险；ma_20清仓和-8%硬止损保留",
            "example_sequence": [
                "先按20日涨幅、MA20斜率、量能比判断trend/swing",
                "trend: MA5/MA10/RSI过热 → 风险提示，不强制减仓",
                "swing: 破MA10 → 清仓",
                "swing: 盈利15%后破MA5，或盈利20%后从高点回撤8% → 减半",
                "任一模式: 破MA20或-8%硬止损 → 清仓",
            ],
        },
        "params": strategy_params.TREND_PULLBACK_PARAMS,
        "robustness_param_grid": strategy_params.ROBUSTNESS_PARAM_GRID,
        "todo_confirm_items": strategy_params.TODO_CONFIRM_ITEMS,
        "reference_intraday_stop_pct": strategy_params.REFERENCE_INTRADAY_STOP_PCT,
        "paper_account": args.paper_account,
        "paper_db_path": paper_db_path,
        "reduce_state_applied": apply_reduce_state,
        "reduce_state_note": (
            None
            if apply_reduce_state
            else (
                "未使用 --paper-account 或已加 --no-apply-reduce-state，减仓「仅一次」状态不会写入数据库"
                if holdings
                else None
            )
        ),
        "reduce": reduce_signals,
        "buy": {
            "from_previous_day_close": buy_prev_out,
            "from_last_close": buy_last_out,
            "from_previous_day_close_raw": buy_prev_raw,
            "from_last_close_raw": buy_last_raw,
            "from_previous_day_close_total": len(buy_prev),
            "from_last_close_total": len(buy_last),
            "from_previous_day_close_raw_total": len(buy_prev_raw),
            "from_last_close_raw_total": len(buy_last_raw),
        },
        "sell": sell_signals,
        "errors_sample": errors[:20],
        "errors_total": len(errors),
    }

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    _print_human_report(payload, holdings=holdings, provider=provider)


if __name__ == "__main__":
    main()
