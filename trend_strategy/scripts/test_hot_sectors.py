#!/usr/bin/env python3
"""Unit tests for hot sector matching and entry fit."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from strategy_lab.hot_sectors import (  # noqa: E402
    evaluate_hot_sector_entry_fit,
    evaluate_hot_sector_match,
)


def _hot_snapshot() -> dict:
    return {
        "industry_pool_names": ["白酒", "银行"],
        "concept_pool_names": ["先进封装", "光伏"],
        "industry_pool": [
            {"groupLabel": "白酒", "changePct": 8.0},
            {"groupLabel": "银行", "changePct": 2.0},
        ],
        "concept_pool": [
            {"groupLabel": "先进封装", "changePct": 10.0},
            {"groupLabel": "光伏", "changePct": 5.0},
        ],
    }


def test_industry_and_concept_required() -> None:
    hot = _hot_snapshot()
    info = {"industry": "白酒行业", "concepts": ["先进封装", "华为概念"]}
    out = evaluate_hot_sector_match(info, hot)
    assert out["hot_sector_matched"] is True
    assert out["matched_hot_industry"] == "白酒"
    assert "先进封装" in out["matched_hot_concepts"]
    assert out["sector_ref_change_pct"] == 10.0


def test_industry_only_not_enough() -> None:
    hot = _hot_snapshot()
    info = {"industry": "白酒行业", "concepts": ["锂电池"]}
    out = evaluate_hot_sector_match(info, hot)
    assert out["hot_sector_matched"] is False


def test_entry_fit_relative_to_sector() -> None:
    closes = [10.0] * 4 + [10.4]
    df = pd.DataFrame({"close": closes})
    match = {"hot_sector_matched": True, "sector_ref_change_pct": 8.0}
    params = {"fast": 3, "hot_sector_pullback_ret_days": 4, "hot_pullback_ceiling": 1.005}
    out = evaluate_hot_sector_entry_fit(df, -1, match, params)
    assert out["hot_sector_entry_fit_passed"] is True
    assert out["fit_reason"] == "stock_weaker_than_sector"


def test_entry_fit_tight_to_ma() -> None:
    closes = [10.0, 10.0, 10.0, 10.0, 10.02]
    df = pd.DataFrame({"close": closes})
    match = {"hot_sector_matched": True, "sector_ref_change_pct": 0.1}
    params = {"fast": 3, "hot_sector_pullback_ret_days": 4, "hot_pullback_ceiling": 1.005}
    out = evaluate_hot_sector_entry_fit(df, -1, match, params)
    assert out["hot_sector_entry_fit_passed"] is True
    assert out["fit_reason"] == "tight_to_ma_fast"


if __name__ == "__main__":
    test_industry_and_concept_required()
    test_industry_only_not_enough()
    test_entry_fit_relative_to_sector()
    test_entry_fit_tight_to_ma()
    print("ok")
