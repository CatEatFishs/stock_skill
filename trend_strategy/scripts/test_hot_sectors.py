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
    evaluate_style_filter,
)

BLACKLIST = ["银行", "保险", "高速公路"]


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


def test_industry_match() -> None:
    hot = _hot_snapshot()
    info = {"industry": "白酒行业", "concepts": []}
    out = evaluate_hot_sector_match(info, hot)
    assert out["hot_sector_matched"] is True
    assert out["matched_hot_industry"] == "白酒"


def test_concept_match() -> None:
    hot = _hot_snapshot()
    info = {"industry": "半导体", "concepts": ["先进封装", "华为概念"]}
    out = evaluate_hot_sector_match(info, hot)
    assert out["hot_sector_matched"] is True
    assert "先进封装" in out["matched_hot_concepts"]


def test_industry_or_concept_either_enough() -> None:
    hot = _hot_snapshot()
    info = {"industry": "白酒行业", "concepts": ["锂电池"]}
    out = evaluate_hot_sector_match(info, hot)
    assert out["hot_sector_matched"] is True
    assert out["matched_hot_industry"] == "白酒"


def test_no_match() -> None:
    hot = _hot_snapshot()
    info = {"industry": "酿酒行业", "concepts": ["锂电池"]}
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


def test_style_filter_blocks_blacklisted_industry() -> None:
    info = {"industry": "银行Ⅱ", "concepts": ["数字货币"]}
    out = evaluate_style_filter(info, industry_blacklist=BLACKLIST, min_concepts=1)
    assert out["style_filter_passed"] is False
    assert out["style_reject_reason"].startswith("industry_blacklisted")


def test_style_filter_blocks_no_concept() -> None:
    info = {"industry": "半导体", "concepts": []}
    out = evaluate_style_filter(info, industry_blacklist=BLACKLIST, min_concepts=1)
    assert out["style_filter_passed"] is False
    assert out["style_reject_reason"].startswith("too_few_concepts")


def test_style_filter_blocks_blacklisted_concept_when_industry_missing() -> None:
    info = {"industry": None, "concepts": ["跨境支付", "区块链", "参股银行"]}
    out = evaluate_style_filter(info, industry_blacklist=BLACKLIST, min_concepts=1)
    assert out["style_filter_passed"] is False
    assert out["style_reject_reason"].startswith("concept_blacklisted")
    assert out["blacklist_hit_source"] == "concept"


def test_style_filter_blocks_bank_concept_tag() -> None:
    info = {"industry": None, "concepts": ["中特估", "银行", "跨境支付"]}
    out = evaluate_style_filter(info, industry_blacklist=["银行"], min_concepts=1)
    assert out["style_filter_passed"] is False
    assert "concept_blacklisted:银行" == out["style_reject_reason"]


def test_style_filter_passes_growth_with_concept() -> None:
    info = {"industry": "半导体", "concepts": ["先进封装", "华为概念"]}
    out = evaluate_style_filter(info, industry_blacklist=BLACKLIST, min_concepts=1)
    assert out["style_filter_passed"] is True
    assert out["style_reject_reason"] is None
    assert out["stock_concept_count"] == 2


if __name__ == "__main__":
    test_industry_match()
    test_concept_match()
    test_industry_or_concept_either_enough()
    test_no_match()
    test_entry_fit_relative_to_sector()
    test_entry_fit_tight_to_ma()
    test_style_filter_blocks_blacklisted_industry()
    test_style_filter_blocks_blacklisted_concept_when_industry_missing()
    test_style_filter_blocks_bank_concept_tag()
    test_style_filter_blocks_no_concept()
    test_style_filter_passes_growth_with_concept()
    print("ok")
