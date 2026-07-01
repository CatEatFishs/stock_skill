"""Default parameters for allmarket_multi_swing_defensive signal logic."""

from __future__ import annotations

STRATEGY_NAME = "allmarket_multi_swing_defensive"

TREND_PULLBACK_PARAMS: dict = {
    "fast": 8,
    "slow": 20,
    "pullback_ceiling": 1.006,
    "rsi_low": 42,
    "rsi_high": 72,
    "bull_rsi_low": 42,
    "bull_rsi_high": 72,
    "bear_rsi_low": 30,
    "bear_rsi_high": 60,
    "exit_rsi": 74,
    "rsi_period": 14,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
}

ROBUSTNESS_PARAM_GRID: dict = {
    "fast": [8, 10],
    "slow": [20, 30],
    "bull_rsi_low": [40, 42],
    "bull_rsi_high": [70, 72],
}

ENTRY_CONSENSUS_MIN_DEFAULT = 0.67

DEFAULT_ROUNDTRIP_COST_BPS = 45.0

TODO_CONFIRM_ITEMS = [
    "已确认口径: roundtrip_cost_bps=45, entry_consensus_min=0.67",
    "已确认口径: bull_rsi=[42,72], bear_rsi=[30,60]",
    "已确认口径: 入场需日K MACD金叉(DIF>DEA)且MACD柱较前一日放大, 参数12/26/9",
    "已确认口径: 减仓状态写入模拟盘库 position_strategy_state 表",
    "已确认口径: 入场需行业与概念均命中近一周涨幅Top30, 且个股5日涨幅<板块周涨幅或贴近ma_8",
    "已确认口径: 大盘环境=沪深300与中证1000收盘>ma_20且RSI>=40才允许新开仓",
    "已确认口径: 已有持仓不出买入信号, 仅减仓/清仓; 各档按剩余仓位比例减仓",
]

REDUCE_EXIT_RSI = 74

UNIVERSE_TOP_N_DEFAULT = 120

MAX_BUY_CANDIDATES = 5

HOT_SECTOR_TOP_N_DEFAULT = 10
HOT_SECTOR_MATCH_POOL_N_DEFAULT = 30
HOT_SECTOR_SORT_DEFAULT = "change_week_desc"
HOT_SECTOR_LOOKBACK_LABEL = "近一周涨幅"
HOT_SECTOR_PULLBACK_RET_DAYS = 5
HOT_SECTOR_TIGHT_PULLBACK_CEILING = 1.005

MARKET_REGIME_INDEX_CODES = {
    "hs300": "000300",
    "zz1000": "000852",
}
MARKET_REGIME_MA_PERIOD = 20
MARKET_REGIME_RSI_PERIOD = 14
MARKET_REGIME_RSI_FLOOR = 40.0

BUY_POLICY_NO_ADD_TO_HOLDINGS = True
REDUCE_BASIS_REMAINING_POSITION = True

REFERENCE_INTRADAY_STOP_PCT = 0.07
