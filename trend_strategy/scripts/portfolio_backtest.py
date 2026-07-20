#!/usr/bin/env python3
"""Portfolio backtest for trend_pullback strategy over a recent date window."""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

try:
    import akshare as ak
except ImportError:
    ak = None  # type: ignore

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from simulated_trading_path import ensure_simulated_trading_on_path

ensure_simulated_trading_on_path()
from simulated_trading.engine import calc_commission, calc_tax  # noqa: E402
from simulated_trading.market_data import MarketDataProvider  # noqa: E402
from strategy_lab import strategy_params
from strategy_lab.hot_sectors import (
    batch_stock_sectors,
    evaluate_hot_sector_entry_fit,
    evaluate_hot_sector_match,
    evaluate_style_filter,
    load_hot_sector_snapshot,
)
from strategy_lab.market_regime import evaluate_market_regime
from strategy_lab.position_reduce import empty_reduce_state, enrich_position_management, evaluate_reduce_signal
from strategy_lab.strategies import trend_pullback


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


def _entry_consensus_ratio(df: pd.DataFrame, variants: list[dict]) -> float:
    if df is None or df.empty or not variants:
        return 0.0
    idx = -1
    votes = valid = 0
    for params in variants:
        try:
            enriched = trend_pullback(df, params)
            if enriched is None or enriched.empty:
                continue
            valid += 1
            if bool(enriched.iloc[idx].get("entry", False)):
                votes += 1
        except Exception:
            continue
    return (votes / valid) if valid else 0.0


def _slice_asof(df: pd.DataFrame, asof: pd.Timestamp) -> pd.DataFrame:
    out = df[df["time"] <= asof].copy()
    return out.reset_index(drop=True)


def _bar_on_date(df: pd.DataFrame, day: pd.Timestamp) -> pd.Series | None:
    if df is None or df.empty:
        return None
    hits = df[df["time"].dt.normalize() == day.normalize()]
    if hits.empty:
        return None
    return hits.iloc[-1]


def _holding_trading_days(df: pd.DataFrame, buy_date: str, day: pd.Timestamp) -> int | None:
    try:
        start = pd.to_datetime(buy_date).normalize()
        end = pd.to_datetime(day).normalize()
    except Exception:
        return None
    if end < start:
        return None
    dates = pd.to_datetime(df["time"], errors="coerce").dt.normalize()
    return int(((dates >= start) & (dates <= end)).sum())


def _peak_close_since(df: pd.DataFrame, buy_date: str, day: pd.Timestamp) -> float | None:
    try:
        start = pd.to_datetime(buy_date).normalize()
        end = pd.to_datetime(day).normalize()
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


@dataclass
class Position:
    code: str
    qty: int
    avg_cost: float
    buy_date: str
    reduce_state: dict[str, bool] = field(default_factory=empty_reduce_state)


def _akshare_available() -> bool:
    return ak is not None


def _normalize_stock_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if "time" not in out.columns:
        rename = {"日期": "time", "开盘": "open", "最高": "high", "最低": "low", "收盘": "close", "成交量": "volume"}
        out = out.rename(columns={k: v for k, v in rename.items() if k in out.columns})
    out["time"] = pd.to_datetime(out["time"], errors="coerce")
    for col in ["open", "high", "low", "close", "volume"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["time", "close"]).sort_values("time").reset_index(drop=True)
    return out[["time", "open", "high", "low", "close", "volume"]]


def _load_index_history_ak(symbol: str) -> pd.DataFrame:
    if not _akshare_available():
        raise RuntimeError("akshare not installed")
    raw = ak.stock_zh_index_daily(symbol=symbol)
    out = raw.rename(columns={"date": "time"})
    return _normalize_stock_df(out)


def _load_stock_history_ak(code: str, start: str, end: str) -> pd.DataFrame | None:
    if not _akshare_available():
        return None
    try:
        raw = ak.stock_zh_a_hist(
            symbol=str(code).zfill(6)[-6:],
            period="daily",
            start_date=pd.to_datetime(start).strftime("%Y%m%d"),
            end_date=pd.to_datetime(end).strftime("%Y%m%d"),
            adjust="qfq",
        )
        if raw is None or raw.empty:
            return None
        return _normalize_stock_df(raw)
    except Exception:
        return None


FALLBACK_UNIVERSE = [
    "600519", "300750", "601318", "600036", "000858", "002594", "601012", "600900",
    "000001", "601166", "600030", "300059", "688981", "002475", "601888", "000333",
    "600276", "002371", "603259", "601899", "300274", "002230", "600809", "000568",
    "601398", "600050", "002415", "300502", "601688", "688041", "600887", "000651",
]


def _load_universe_ak(top_n: int) -> list[str]:
    if not _akshare_available():
        return FALLBACK_UNIVERSE[:top_n]
    from simulated_trading.market_data import filter_a_share_universe

    for attempt in range(3):
        try:
            df = ak.stock_zh_a_spot_em()
            if df is not None and not df.empty:
                return filter_a_share_universe(df, include_growth_boards=True, top_n=top_n)
        except Exception:
            if attempt < 2:
                import time
                time.sleep(1.5 * (attempt + 1))
    return FALLBACK_UNIVERSE[:top_n]


def _fetch_histories(
    provider: MarketDataProvider,
    codes: list[str],
    count: int,
    workers: int,
    *,
    start: str,
    end: str,
) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}

    def one(code: str) -> tuple[str, pd.DataFrame | None]:
        for attempt in range(2):
            df = _load_stock_history_ak(code, start, end)
            if df is not None and not df.empty:
                return code, df.tail(count).reset_index(drop=True)
            if attempt == 0:
                import time
                time.sleep(0.3)
        try:
            df = provider.get_history(code, count=count)
            return code, df
        except Exception:
            return code, None

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {pool.submit(one, c): c for c in codes}
        for fut in as_completed(futs):
            code, df = fut.result()
            if df is not None and not df.empty:
                out[code] = df
    return out


def _market_regime_on_day(
    index_histories: dict[str, pd.DataFrame],
    day: pd.Timestamp,
    *,
    ma_period: int,
    rsi_period: int,
    rsi_floor: float,
) -> dict[str, Any]:
    class _IdxProvider:
        def get_history(self, symbol: str, count: int = 60) -> pd.DataFrame:
            df = index_histories.get(symbol)
            if df is None:
                raise ValueError(f"no index history for {symbol}")
            sliced = _slice_asof(df, day)
            return sliced.tail(max(count, ma_period + rsi_period + 5)).reset_index(drop=True)

    code_map = strategy_params.MARKET_REGIME_INDEX_CODES
    return evaluate_market_regime(
        _IdxProvider(),
        code_map,
        ma_period=ma_period,
        rsi_period=rsi_period,
        rsi_floor=rsi_floor,
        bar_idx=-1,
    )


def _scan_buy_candidates(
    universe: list[str],
    histories: dict[str, pd.DataFrame],
    asof: pd.Timestamp,
    holdings: set[str],
    *,
    param_variants: list[dict],
    entry_params: dict,
    hot_snapshot: dict,
    sector_map: dict[str, dict],
    roundtrip_cost_bps: float,
    entry_consensus_min: float,
    hot_filter_active: bool,
    regime_ok: bool,
    max_buys: int,
) -> list[dict]:
    if not regime_ok:
        return []
    rows: list[dict] = []
    min_bars = max(int(entry_params.get("slow", 20)) + 3, 30)
    for code in universe:
        if code in holdings:
            continue
        raw = histories.get(code)
        if raw is None:
            continue
        df = _slice_asof(raw, asof)
        if len(df) < min_bars:
            continue
        enriched = trend_pullback(df, entry_params)
        row = enriched.iloc[-1]
        if not bool(row.get("entry", False)):
            continue
        score = float(row.get("score", 0.0))
        edge = max(score, 0.0) - max(roundtrip_cost_bps, 0.0) / 10000.0
        consensus = _entry_consensus_ratio(df, param_variants)
        if edge <= 0 or consensus < entry_consensus_min:
            continue
        style_info = evaluate_style_filter(
            sector_map.get(code),
            industry_blacklist=strategy_params.INDUSTRY_BLACKLIST,
            min_concepts=strategy_params.REQUIRE_MIN_STOCK_CONCEPTS,
        )
        if not bool(style_info.get("style_filter_passed")):
            continue
        match_info = evaluate_hot_sector_match(sector_map.get(code), hot_snapshot)
        fit_info = evaluate_hot_sector_entry_fit(df, -1, match_info, entry_params)
        hot_ok = True
        if hot_filter_active:
            hot_ok = bool(match_info.get("hot_sector_matched")) and bool(
                fit_info.get("hot_sector_entry_fit_passed")
            )
        if not hot_ok:
            continue
        rows.append(
            {
                "code": code,
                "score": score,
                "signal_date": row["time"].strftime("%Y-%m-%d"),
                "close": round(float(row["close"]), 4),
            }
        )
    rows.sort(key=lambda x: x["score"], reverse=True)
    cap = int(max_buys)
    return rows if cap <= 0 else rows[:cap]


def run_backtest(args: argparse.Namespace) -> dict[str, Any]:
    provider = MarketDataProvider()
    end_day = pd.Timestamp(args.end) if args.end else pd.Timestamp(datetime.now().date())
    start_day = pd.Timestamp(args.start) if args.start else end_day - pd.Timedelta(days=int(args.days) + 15)

    index_histories: dict[str, pd.DataFrame] = {}
    for name, code in strategy_params.MARKET_REGIME_INDEX_CODES.items():
        try:
            index_histories[code] = _load_index_history_ak(code)
        except Exception as exc:
            try:
                index_histories[code] = provider.get_history(code, count=180)
            except Exception as exc2:
                raise RuntimeError(f"failed to load index {name} ({code}): {exc}; {exc2}") from exc2

    cal_key = next(iter(strategy_params.MARKET_REGIME_INDEX_CODES.values()))
    cal = _slice_asof(index_histories[cal_key], end_day)
    trading_days = sorted(
        d for d in cal["time"].dt.normalize().unique() if start_day <= d <= end_day
    )
    min_days = max(2, int(args.trading_days))
    if len(trading_days) < min_days:
        raise RuntimeError(f"not enough trading days in window: {len(trading_days)} < {min_days}")

    # Use last N trading days
    window_days = trading_days[-int(args.trading_days) :]
    hist_start = (window_days[0] - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
    hist_end = window_days[-1].strftime("%Y-%m-%d")

    universe = _load_universe_ak(int(args.top_n))
    if not universe:
        universe = provider.get_all_market_universe(as_of=None, top_n=int(args.top_n))
    if not universe:
        raise RuntimeError("empty universe")

    histories = _fetch_histories(
        provider,
        universe,
        count=int(args.history_count),
        workers=int(args.workers),
        start=hist_start,
        end=hist_end,
    )
    if not histories:
        raise RuntimeError("failed to load any symbol history")

    hot_snapshot = load_hot_sector_snapshot(
        top_n=int(args.hot_sector_top_n),
        match_pool_n=int(args.hot_sector_pool_n),
        sort=strategy_params.HOT_SECTOR_SORT_DEFAULT,
    )
    hot_filter_active = not bool(args.disable_hot_sector_check) and bool(
        hot_snapshot.get("industry_pool_names") or hot_snapshot.get("concept_pool_names")
    )
    sector_map = batch_stock_sectors(universe, workers=max(1, int(args.workers)))

    entry_params = dict(strategy_params.TREND_PULLBACK_PARAMS)
    entry_params["hot_sector_pullback_ret_days"] = strategy_params.HOT_SECTOR_PULLBACK_RET_DAYS
    entry_params["hot_pullback_ceiling"] = strategy_params.HOT_SECTOR_TIGHT_PULLBACK_CEILING
    param_variants = _build_param_variants(entry_params, strategy_params.ROBUSTNESS_PARAM_GRID)
    reduce_params = dict(entry_params)

    cash = float(args.cash)
    initial_cash = cash
    positions: dict[str, Position] = {}
    pending_buys: list[dict] = []
    trades: list[dict] = []
    equity_curve: list[dict] = []

    max_positions = int(args.max_positions)

    for i, day in enumerate(window_days):
        day_s = day.strftime("%Y-%m-%d")

        # 1) Execute pending buys at open
        if pending_buys and i > 0:
            slots = max(0, max_positions - len(positions))
            for item in pending_buys[:slots]:
                code = item["code"]
                if code in positions:
                    continue
                raw = histories.get(code)
                if raw is None:
                    continue
                bar = _bar_on_date(raw, day)
                if bar is None:
                    continue
                price = float(bar["open"]) if pd.notna(bar["open"]) and float(bar["open"]) > 0 else float(bar["close"])
                if price <= 0:
                    continue
                slots_left = max(1, max_positions - len(positions))
                budget = cash * 0.98 / slots_left
                lot_qty = int(budget / price // 100) * 100
                if lot_qty < 100:
                    continue
                amount = lot_qty * price
                commission = calc_commission(amount, code)
                total = amount + commission
                if total > cash:
                    lot_qty = int((cash - commission) / price // 100) * 100
                    if lot_qty < 100:
                        continue
                    amount = lot_qty * price
                    commission = calc_commission(amount, code)
                    total = amount + commission
                cash -= total
                positions[code] = Position(code=code, qty=lot_qty, avg_cost=price, buy_date=day_s)
                trades.append(
                    {
                        "date": day_s,
                        "code": code,
                        "action": "buy",
                        "price": round(price, 3),
                        "qty": lot_qty,
                        "amount": round(amount, 2),
                        "commission": commission,
                        "signal_date": item.get("signal_date"),
                    }
                )
        pending_buys = []

        # 2) Reduce / clear at close. Early holding days only allow MA20 clear or hard stop.
        for code in list(positions.keys()):
            pos = positions[code]
            raw = histories.get(code)
            if raw is None:
                continue
            df = _slice_asof(raw, day)
            if len(df) < 25:
                continue
            mgmt = enrich_position_management(df, reduce_params)
            sig = evaluate_reduce_signal(
                mgmt.iloc[-1],
                pos.reduce_state,
                reduce_params,
                holding_trading_days=_holding_trading_days(raw, pos.buy_date, day),
                avg_cost=pos.avg_cost,
                peak_close=_peak_close_since(raw, pos.buy_date, day),
            )
            if not sig:
                continue
            if sig["action"] == "watch":
                flag = sig.get("state_flag")
                if flag:
                    pos.reduce_state[flag] = True
                continue
            bar = mgmt.iloc[-1]
            price = float(bar["close"])
            if sig["action"] == "clear":
                sell_qty = pos.qty
            else:
                sell_qty = int(pos.qty * float(sig["reduce_ratio"]) // 100) * 100
                if sell_qty < 100 and pos.qty >= 100:
                    sell_qty = 100
                sell_qty = min(sell_qty, pos.qty)
            if sell_qty <= 0:
                continue
            amount = sell_qty * price
            commission = calc_commission(amount, code)
            tax = calc_tax("sell", amount)
            proceeds = amount - commission - tax
            cost_basis = sell_qty * pos.avg_cost
            profit = proceeds - cost_basis
            cash += proceeds
            trades.append(
                {
                    "date": day_s,
                    "code": code,
                    "action": "sell" if sig["action"] == "clear" else "reduce",
                    "reason": sig["reason"],
                    "position_mode": sig.get("position_mode"),
                    "reduce_pct_label": sig["reduce_pct_label"],
                    "price": round(price, 3),
                    "qty": sell_qty,
                    "amount": round(amount, 2),
                    "profit": round(profit, 2),
                }
            )
            flag = sig.get("state_flag")
            if flag:
                pos.reduce_state[flag] = True
            pos.qty -= sell_qty
            if pos.qty <= 0 or sig["action"] == "clear":
                del positions[code]
                pos.reduce_state = empty_reduce_state()

        # 3) Schedule buys for next trading day (signal on today's close)
        if i < len(window_days) - 1:
            regime = _market_regime_on_day(
                index_histories,
                day,
                ma_period=strategy_params.MARKET_REGIME_MA_PERIOD,
                rsi_period=strategy_params.MARKET_REGIME_RSI_PERIOD,
                rsi_floor=strategy_params.MARKET_REGIME_RSI_FLOOR,
            )
            pending_buys = _scan_buy_candidates(
                universe,
                histories,
                day,
                set(positions.keys()),
                param_variants=param_variants,
                entry_params=entry_params,
                hot_snapshot=hot_snapshot,
                sector_map=sector_map,
                roundtrip_cost_bps=float(args.roundtrip_cost_bps),
                entry_consensus_min=float(args.entry_consensus_min),
                hot_filter_active=hot_filter_active,
                regime_ok=bool(regime.get("allow_new_buys")),
                max_buys=int(args.max_buys),
            )

        # Mark to market at close
        mkt = 0.0
        for code, pos in positions.items():
            raw = histories.get(code)
            bar = _bar_on_date(raw, day) if raw is not None else None
            px = float(bar["close"]) if bar is not None else pos.avg_cost
            mkt += pos.qty * px
        equity = cash + mkt
        equity_curve.append(
            {
                "date": day_s,
                "cash": round(cash, 2),
                "market_value": round(mkt, 2),
                "equity": round(equity, 2),
                "positions": len(positions),
            }
        )

    final_equity = equity_curve[-1]["equity"] if equity_curve else initial_cash
    peak = 0.0
    max_dd = 0.0
    for pt in equity_curve:
        peak = max(peak, pt["equity"])
        if peak > 0:
            max_dd = max(max_dd, (peak - pt["equity"]) / peak)

    sell_trades = [t for t in trades if t["action"] in {"sell", "reduce"}]
    realized = sum(float(t.get("profit", 0.0)) for t in sell_trades)

    return {
        "strategy": strategy_params.STRATEGY_NAME,
        "period": {
            "start": window_days[0].strftime("%Y-%m-%d"),
            "end": window_days[-1].strftime("%Y-%m-%d"),
            "trading_days": len(window_days),
        },
        "initial_cash": round(initial_cash, 2),
        "final_equity": round(final_equity, 2),
        "total_return_pct": round((final_equity - initial_cash) / initial_cash * 100.0, 2),
        "max_drawdown_pct": round(max_dd * 100.0, 2),
        "realized_pnl": round(realized, 2),
        "trade_count": len(trades),
        "buy_count": len([t for t in trades if t["action"] == "buy"]),
        "sell_reduce_count": len(sell_trades),
        "open_positions_end": [
            {"code": p.code, "qty": p.qty, "avg_cost": round(p.avg_cost, 3)} for p in positions.values()
        ],
        "limitations": [
            "股票池为回测期末成交额Top-N快照，非逐日动态池",
            "热门板块/个股行业概念使用回测运行时截面数据，非历史逐日榜单",
        ],
        "equity_curve": equity_curve,
        "trades": trades,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Portfolio backtest for trend_pullback")
    parser.add_argument("--days", type=int, default=35, help="Calendar lookback to find trading days")
    parser.add_argument("--trading-days", type=int, default=5, help="Number of trading days to simulate (~1 week)")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD (optional)")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--cash", type=float, default=1_000_000.0)
    parser.add_argument("--top-n", type=int, default=strategy_params.UNIVERSE_TOP_N_DEFAULT)
    parser.add_argument("--max-positions", type=int, default=strategy_params.MAX_BUY_CANDIDATES)
    parser.add_argument("--max-buys", type=int, default=strategy_params.MAX_BUY_CANDIDATES)
    parser.add_argument("--history-count", type=int, default=150)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--roundtrip-cost-bps", type=float, default=strategy_params.DEFAULT_ROUNDTRIP_COST_BPS)
    parser.add_argument("--entry-consensus-min", type=float, default=strategy_params.ENTRY_CONSENSUS_MIN_DEFAULT)
    parser.add_argument("--hot-sector-top-n", type=int, default=strategy_params.HOT_SECTOR_TOP_N_DEFAULT)
    parser.add_argument("--hot-sector-pool-n", type=int, default=strategy_params.HOT_SECTOR_MATCH_POOL_N_DEFAULT)
    parser.add_argument("--disable-hot-sector-check", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = run_backtest(args)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        p = result["period"]
        print("回测区间", p["start"], "~", p["end"], f"({p['trading_days']} 个交易日)")
        print("初始资金", result["initial_cash"])
        print("期末净值", result["final_equity"])
        print("收益率", f"{result['total_return_pct']}%")
        print("最大回撤", f"{result['max_drawdown_pct']}%")
        print("交易笔数", result["trade_count"], "买入", result["buy_count"], "卖/减", result["sell_reduce_count"])


if __name__ == "__main__":
    main()
