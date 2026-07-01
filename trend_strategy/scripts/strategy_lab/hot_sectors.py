"""Hot sector gate for entry signals (DangInvest top boards + Eastmoney stock sectors)."""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from base_data_path import ensure_base_data_on_path


def _normalize_label(label: str | None) -> str:
    if not label:
        return ""
    text = str(label).strip()
    text = re.sub(r"\s+", "", text)
    return text


def _labels_match(a: str, b: str) -> bool:
    na, nb = _normalize_label(a), _normalize_label(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if len(na) >= 2 and len(nb) >= 2 and (na in nb or nb in na):
        return True
    return False


def _board_item_view(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "groupLabel": item.get("groupLabel"),
        "groupKey": item.get("groupKey"),
        "changePct": item.get("changePct"),
    }


def load_hot_sector_snapshot(
    top_n: int = 10,
    sort: str = "change_week_desc",
    *,
    match_pool_n: int = 30,
    include_sub_industry: bool = True,
    include_concept: bool = True,
) -> dict[str, Any]:
    ensure_base_data_on_path()
    from fetch_danginvest import fetch_boards_summary_items

    pool_n = max(int(top_n), int(match_pool_n))
    snapshot: dict[str, Any] = {
        "top_n": int(top_n),
        "match_pool_n": int(match_pool_n),
        "sort": sort,
        "lookback": "1w",
        "lookback_label": "近一周涨幅",
        "match_mode": "industry_and_concept_in_pool",
        "trade_date": None,
        "industry_top": [],
        "concept_top": [],
        "industry_pool": [],
        "concept_pool": [],
        "industry_names": [],
        "concept_names": [],
        "industry_pool_names": [],
        "concept_pool_names": [],
        "error": None,
    }
    errors: list[str] = []

    if include_sub_industry:
        try:
            items, meta = fetch_boards_summary_items("sub", limit=pool_n, sort=sort)
            pool = [_board_item_view(item) for item in items]
            snapshot["industry_pool"] = pool[:match_pool_n]
            snapshot["industry_top"] = pool[:top_n]
            snapshot["industry_pool_names"] = [
                str(x.get("groupLabel") or "").strip()
                for x in snapshot["industry_pool"]
                if x.get("groupLabel")
            ]
            snapshot["industry_names"] = snapshot["industry_pool_names"][:top_n]
            snapshot["trade_date"] = meta.get("tradeDate") or snapshot["trade_date"]
        except Exception as exc:
            errors.append(f"industry:{exc}")

    if include_concept:
        try:
            items, meta = fetch_boards_summary_items("concept", limit=pool_n, sort=sort)
            pool = [_board_item_view(item) for item in items]
            snapshot["concept_pool"] = pool[:match_pool_n]
            snapshot["concept_top"] = pool[:top_n]
            snapshot["concept_pool_names"] = [
                str(x.get("groupLabel") or "").strip()
                for x in snapshot["concept_pool"]
                if x.get("groupLabel")
            ]
            snapshot["concept_names"] = snapshot["concept_pool_names"][:top_n]
            snapshot["trade_date"] = meta.get("tradeDate") or snapshot["trade_date"]
        except Exception as exc:
            errors.append(f"concept:{exc}")

    if errors:
        snapshot["error"] = "; ".join(errors)[:500]
    return snapshot


def _find_pool_item(label: str | None, pool: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not label:
        return None
    for item in pool:
        if _labels_match(label, str(item.get("groupLabel") or "")):
            return item
    return None


def evaluate_hot_sector_match(
    stock_sector: dict[str, Any] | None,
    hot_snapshot: dict[str, Any],
) -> dict[str, Any]:
    industry = (stock_sector or {}).get("industry")
    concepts = list((stock_sector or {}).get("concepts") or [])
    hot_industries = list(
        hot_snapshot.get("industry_pool_names") or hot_snapshot.get("industry_names") or []
    )
    hot_concepts = list(
        hot_snapshot.get("concept_pool_names") or hot_snapshot.get("concept_names") or []
    )
    industry_pool = list(hot_snapshot.get("industry_pool") or [])
    concept_pool = list(hot_snapshot.get("concept_pool") or [])

    matched_industry: str | None = None
    matched_industry_change_pct: float | None = None
    matched_concepts: list[str] = []
    matched_concept_changes: list[float] = []

    for hot in hot_industries:
        if _labels_match(industry, hot):
            matched_industry = hot
            item = _find_pool_item(hot, industry_pool)
            if item and item.get("changePct") is not None:
                matched_industry_change_pct = float(item["changePct"])
            break

    for concept in concepts:
        for hot in hot_concepts:
            if _labels_match(concept, hot):
                matched_concepts.append(hot)
                item = _find_pool_item(hot, concept_pool)
                if item and item.get("changePct") is not None:
                    matched_concept_changes.append(float(item["changePct"]))
                break

    matched_concepts = list(dict.fromkeys(matched_concepts))
    matched = bool(matched_industry and matched_concepts)

    sector_ref_change_pct: float | None = None
    refs = [x for x in [matched_industry_change_pct, *matched_concept_changes] if x is not None]
    if refs:
        sector_ref_change_pct = max(refs)

    return {
        "hot_sector_matched": matched,
        "hot_sector_match_mode": "industry_and_concept_in_pool",
        "stock_industry": industry,
        "stock_concepts": concepts,
        "matched_hot_industry": matched_industry,
        "matched_hot_industry_change_pct": matched_industry_change_pct,
        "matched_hot_concepts": matched_concepts,
        "matched_hot_concept_change_pcts": matched_concept_changes,
        "sector_ref_change_pct": sector_ref_change_pct,
    }


def evaluate_hot_sector_entry_fit(
    history_df: pd.DataFrame | None,
    bar_idx: int,
    match_info: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    """Pullback within hot sector: 5d stock return below sector weekly change OR tight to ma_fast."""
    ret_days = int(params.get("hot_sector_pullback_ret_days", 5))
    fast = int(params.get("fast", 8))
    tight_ceiling = float(params.get("hot_pullback_ceiling", params.get("hot_sector_tight_pullback_ceiling", 1.005)))

    out: dict[str, Any] = {
        "hot_sector_entry_fit_passed": False,
        "stock_ret_nd_pct": None,
        "sector_ref_change_pct": match_info.get("sector_ref_change_pct"),
        "near_ma_fast": False,
        "relative_to_sector_ok": False,
        "fit_reason": None,
    }

    if not bool(match_info.get("hot_sector_matched")):
        out["fit_reason"] = "no_hot_match"
        return out

    if history_df is None or history_df.empty or abs(bar_idx) > len(history_df):
        out["fit_reason"] = "no_history"
        return out

    idx = bar_idx if bar_idx >= 0 else len(history_df) + bar_idx
    row = history_df.iloc[idx]
    close = float(row["close"])
    ma_fast = history_df["close"].rolling(fast).mean().iloc[idx]
    if pd.isna(ma_fast):
        out["fit_reason"] = "no_ma_fast"
        return out

    near_ma_fast = close <= float(ma_fast) * tight_ceiling
    out["near_ma_fast"] = near_ma_fast

    stock_ret_pct: float | None = None
    if idx >= ret_days:
        base_close = float(history_df.iloc[idx - ret_days]["close"])
        if base_close > 0:
            stock_ret_pct = (close / base_close - 1.0) * 100.0
            out["stock_ret_nd_pct"] = round(stock_ret_pct, 4)

    sector_pct = match_info.get("sector_ref_change_pct")
    relative_ok = False
    if stock_ret_pct is not None and sector_pct is not None:
        relative_ok = stock_ret_pct < float(sector_pct)
    out["relative_to_sector_ok"] = relative_ok

    if relative_ok:
        out["hot_sector_entry_fit_passed"] = True
        out["fit_reason"] = "stock_weaker_than_sector"
    elif near_ma_fast:
        out["hot_sector_entry_fit_passed"] = True
        out["fit_reason"] = "tight_to_ma_fast"
    else:
        out["fit_reason"] = "chasing_sector"

    return out


def batch_stock_sectors(codes: list[str], workers: int = 8, timeout: int = 10) -> dict[str, dict[str, Any]]:
    if not codes:
        return {}
    ensure_base_data_on_path()
    from fetch_sector_info import batch_get_sector_info

    results = batch_get_sector_info(codes, timeout=timeout, max_workers=workers, include_concepts=True)
    out: dict[str, dict[str, Any]] = {}
    for row in results:
        code = str(row.get("code") or "").zfill(6)
        if code:
            out[code] = row
    return out
