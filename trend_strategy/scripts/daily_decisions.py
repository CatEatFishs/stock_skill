#!/usr/bin/env python3
"""Scan all-market liquidity pool and emit buy/sell signals (no backtest, no order execution)."""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
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
        item.update(match_info)
        item.update(fit_info)
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
        help="Industry/concept match pool size by weekly change (default 30, both required)",
    )
    parser.add_argument(
        "--disable-market-regime-check",
        action="store_true",
        help="Disable index regime gate (HS300+ZZ1000 above ma_20 and RSI floor)",
    )
    args = parser.parse_args()

    holdings_file = _parse_holdings(args.holdings) if args.holdings else []
    paper_engine = None
    paper_db_path = None
    paper_codes: list[str] = []
    if args.paper_account:
        try:
            paper_engine, paper_db_path = _import_simulated_engine(args.db_path)
            positions = paper_engine.get_positions(args.paper_account)
            paper_codes = [_normalize_code(item["symbol"]) for item in positions if int(item.get("qty") or 0) > 0]
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
        bar_idx=-2,
    )
    market_regime_last = evaluate_market_regime(
        provider,
        strategy_params.MARKET_REGIME_INDEX_CODES,
        history_count=int(args.history_count),
        ma_period=strategy_params.MARKET_REGIME_MA_PERIOD,
        rsi_period=strategy_params.MARKET_REGIME_RSI_PERIOD,
        rsi_floor=strategy_params.MARKET_REGIME_RSI_FLOOR,
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

    def _passes_buy_filters(item: dict, *, use_previous_day: bool) -> bool:
        ok = bool(item.get("cost_filter_passed")) and bool(item.get("consensus_filter_passed"))
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

    buy_prev.sort(key=lambda x: float(x["signal_bar"]["score"]), reverse=True)
    buy_last.sort(key=lambda x: float(x["signal_bar"]["score"]), reverse=True)

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
            reduce_sig = evaluate_reduce_signal(row, position_state, reduce_params)
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
            if apply_reduce_state and paper_engine is not None and args.paper_account:
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
        "reduce_policy": {
            "basis": "remaining_position",
            "note": "各档减仓比例按触发时剩余仓位计算，非初始总仓位",
            "example_sequence": [
                "RSI过热减1/3 → 剩2/3",
                "破ma_5再减1/3 → 约剩4/9",
                "破ma_10减1/2 → 约剩2/9",
                "破ma_20清仓",
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

    print("strategy", payload["strategy"])
    print("latest_bar_date", latest_bar_date)
    print("universe_size", len(universe))
    if cap > 0:
        print("max_buy_candidates", cap, "shown", len(buy_prev_out), "/", len(buy_prev), "and", len(buy_last_out), "/", len(buy_last))
    print()
    print("=== 买入参考：上一交易日收盘出现 entry（适合与 T-1 信号、当日执行对齐）===")
    for item in buy_prev_out:
        sb = item["signal_bar"]
        print(
            item["code"],
            item.get("name", ""),
            "score",
            sb["score"],
            "rsi",
            sb["rsi"],
            "date",
            sb["date"],
        )
    if not buy_prev_out:
        print("(无)")
    print()
    print("=== 买入参考：最新一根日线收盘也出现 entry（形态展示，注意与 T-1 语义不同）===")
    for item in buy_last_out:
        sb = item["signal_bar"]
        print(
            item["code"],
            item.get("name", ""),
            "score",
            sb["score"],
            "rsi",
            sb["rsi"],
            "date",
            sb["date"],
        )
    if not buy_last_out:
        print("(无)")
    print()
    print("=== 减仓参考：持仓且最新收盘触发分档减仓（需 --paper-account 才持久化状态）===")
    if holdings:
        for item in reduce_signals:
            if "error" in item:
                print(item["code"], "error", item["error"])
            else:
                sb = item["signal_bar"]
                print(
                    item["code"],
                    item.get("name", ""),
                    item.get("action"),
                    item.get("reduce_pct_label"),
                    item.get("reason"),
                    "date",
                    sb["date"],
                    "close",
                    sb["close"],
                    "rsi",
                    sb.get("rsi"),
                    "state_applied",
                    item.get("state_applied"),
                )
        if not reduce_signals:
            print("(无)")
    else:
        print("未传 --holdings / --paper-account，跳过持仓减仓扫描")
    print()
    print("=== 卖出参考：持仓且最新收盘满足 exit / 减仓清仓 ===")
    if args.holdings:
        for item in sell_signals:
            if "error" in item:
                print(item["code"], "error", item["error"])
            else:
                sb = item["signal_bar"]
                print(
                    item["code"],
                    item.get("name", ""),
                    "exit_date",
                    sb["date"],
                    "close",
                    sb["close"],
                    "rsi",
                    sb["rsi"],
                )
        if not sell_signals:
            print("(无)")
    else:
        print("未传 --holdings / --paper-account，跳过持仓卖出扫描")


if __name__ == "__main__":
    main()
