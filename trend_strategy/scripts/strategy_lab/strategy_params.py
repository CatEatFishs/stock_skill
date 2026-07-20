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
    "early_hold_protect_days": 3,
    "hard_stop_pct": 0.08,
    "trend_mode_ret20_min": 0.25,
    "trend_mode_ma20_slope5_min": 0.02,
    "trend_mode_volume_ratio_min": 1.0,
    "trend_mode_strong_ret20": 0.40,
    "trend_mode_lookback_days": 15,
    "swing_profit_ma5_pct": 0.15,
    "swing_trailing_profit_pct": 0.20,
    "swing_trailing_drawdown_pct": 0.08,
    "swing_rsi_hot": 80,
    "rsi_period": 14,
    "macd_fast": 12,
    "macd_slow": 26,
    "macd_signal": 9,
    "macd_momentum_score_bonus": 0.003,
}

ROBUSTNESS_PARAM_GRID: dict = {
    "fast": [8, 10],
    "slow": [20, 30],
    "bull_rsi_low": [40, 42],
    "bull_rsi_high": [70, 72],
}

ENTRY_CONSENSUS_MIN_DEFAULT = 0.67

DEFAULT_ROUNDTRIP_COST_BPS = 45.0

MACD_MOMENTUM_SCORE_BONUS = 0.003

TODO_CONFIRM_ITEMS = [
    "已确认口径: roundtrip_cost_bps=45, entry_consensus_min=0.67",
    "已确认口径: bull_rsi=[42,72], bear_rsi=[30,60]",
    "已确认口径: MACD动能(DIF>DEA且柱放大)为排序加分项, 非入场必要条件",
    "已确认口径: 减仓状态写入模拟盘库 position_strategy_state 表",
    "已确认口径: 入场需行业或概念任一命中近一周涨幅Top50, 且个股5日涨幅<板块周涨幅或贴近ma_8",
    "已确认口径: 大盘环境=沪深300与中证1000 RSI>=40才允许新开仓(不再要求指数>ma_20)",
    "已确认口径: 已有持仓不出买入信号, 仅减仓/清仓; 各档按剩余仓位比例减仓",
    "已确认口径: 风格过滤=行业黑名单(银行)剔除, 行业缺失时概念命中黑名单也剔除, 且要求至少1个概念标签",
]

REDUCE_EXIT_RSI = 74

UNIVERSE_TOP_N_DEFAULT = 120

MAX_BUY_CANDIDATES = 5

HOT_SECTOR_TOP_N_DEFAULT = 10
HOT_SECTOR_MATCH_POOL_N_DEFAULT = 50
HOT_SECTOR_SORT_DEFAULT = "change_week_desc"
HOT_SECTOR_LOOKBACK_LABEL = "近一周涨幅"
HOT_SECTOR_PULLBACK_RET_DAYS = 5
HOT_SECTOR_TIGHT_PULLBACK_CEILING = 1.005

# 行业黑名单：命中即剔除，支持包含匹配（如"银行Ⅱ"/"国有大型银行Ⅲ"均匹配"银行"）。
INDUSTRY_BLACKLIST: list[str] = [
    "银行",
]

# 方向4：要求个股至少命中 N 个概念标签（无题材的纯蓝筹被剔除）
REQUIRE_MIN_STOCK_CONCEPTS = 1

MARKET_REGIME_INDEX_CODES = {
    "hs300": "sh000300",
    "zz1000": "sh000852",
}
MARKET_REGIME_MA_PERIOD = 20
MARKET_REGIME_RSI_PERIOD = 14
MARKET_REGIME_RSI_FLOOR = 40.0

BUY_POLICY_NO_ADD_TO_HOLDINGS = True
REDUCE_BASIS_REMAINING_POSITION = True

REFERENCE_INTRADAY_STOP_PCT = 0.07

# stock_skill 展示评分（0-100）。这是策略信号强弱评分，不是 BigA 基本面总分。
SIGNAL_SCORE_WEIGHTS: dict = {
    "trend": 25,
    "rsi": 15,
    "robustness": 15,
    "edge_after_cost": 15,
    "hot_sector": 15,
    "style": 5,
    "market_regime": 5,
    "macd_momentum": 5,
}
