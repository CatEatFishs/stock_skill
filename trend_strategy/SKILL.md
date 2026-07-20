---
name: a-share-strategy-allmarket-multi-swing-defensive
description: A 股全市场高流动性池内按趋势回踩（trend_pullback）产出买入、分档减仓与清仓信号，覆盖主板/创业板/科创板；MACD 动能作排序加分，模拟盘减仓状态持久化。Use when 用户要选股、判断买卖/减仓信号、看实时报价或检查持仓离场条件，且不排除创业板和科创板。
---

# 全市场多波段防御型 · 决策信号

基于日线 `trend_pullback`（趋势回踩）策略，从全市场高流动性股票池中扫描**买入候选**，并对持仓输出**减仓风险提示**与**清仓**参考。  
输出为结构化 JSON / 终端列表，**不跑回测、不自动下单**；执行请接 `simulated_trading` 或券商通道。

## 能力边界

| 做 | 不做 |
| --- | --- |
| 全市场（含创业板/科创板）高流动性股票池扫描 | 分钟级回测、收益曲线 |
| 日线入场 `entry`、趋势/波段双模式持仓管理、清仓 `exit`、风险提示 `watch` | 保证收益、替代投顾 |
| 买入侧成本过滤 + 参数鲁棒性过滤 | 直接向交易所报单 |
| 可选 `--paper-account` 持久化减仓「仅一次」状态 | 替你维护实盘持仓（除非自建文件） |
| `realtime_quotes.py` 批量现价快照 | 替代 `base_data` 的板块/资金流重接口 |

---

## 股票池

默认取当日成交额排名前 **120** 只（`--top-n`），代码前缀覆盖：

- 沪市主板：`600/601/603/605`
- 深市主板：`000/001/002`
- 创业板：`300/301`
- 科创板：`688/689`

排除：`ST/*ST`、名称含「退」的标的。

---

## 默认参数

见 `scripts/strategy_lab/strategy_params.py`，核心如下：

| 参数 | 默认值 | 用途 |
| --- | --- | --- |
| `fast` / `slow` | 8 / 20 | 入场均线（`ma_8`、`ma_20`） |
| `pullback_ceiling` | 1.006 | 回踩上沿：`收盘 < ma_8 × 1.006` |
| `bull_rsi_low` / `bull_rsi_high` | 42 / 72 | 多头趋势下 RSI_14 区间 |
| `macd_fast/slow/signal` | 12 / 26 / 9 | 日 K MACD（动能加分，非入场硬条件） |
| `macd_momentum_score_bonus` | 0.003 | MACD 动能命中时加到 `score` |
| `exit_rsi` | 74 | 减仓：RSI 过热阈值 |
| `early_hold_protect_days` | 3 | 买入后前 N 个交易日保护，不触发 ma_5/ma_10 正式减仓 |
| `hard_stop_pct` | 0.08 | 买入后硬止损，收盘较成本跌幅达到 8% 时清仓 |
| `trend_mode_ret20_min` | 0.25 | 持仓判断趋势模式：20 日涨幅下限 |
| `trend_mode_ma20_slope5_min` | 0.02 | 持仓判断趋势模式：MA20 近 5 日斜率下限 |
| `trend_mode_volume_ratio_min` | 1.0 | 持仓判断趋势模式：量能比下限 |
| `trend_mode_strong_ret20` | 0.40 | 强趋势豁免：20 日涨幅超过该值时可放宽量能比 |
| `trend_mode_lookback_days` | 15 | 最近 N 个交易日内出现过趋势条件，且仍在 MA20 上方，则继续按趋势模式管理 |
| `swing_profit_ma5_pct` | 0.15 | 波段模式：盈利达到该比例后跌破 MA5 减半 |
| `swing_trailing_profit_pct` / `swing_trailing_drawdown_pct` | 0.20 / 0.08 | 波段模式：盈利后从持仓高点回撤触发减半 |
| `swing_rsi_hot` | 80 | 波段模式：极热放量阴线减半阈值 |
| `roundtrip_cost_bps` | 45 | 买入成本过滤 |
| `entry_consensus_min` | 0.67 | 买入鲁棒性过滤 |
| `hot_sector_top_n` / `hot_sector_pool_n` | 10 / 50 | 展示 Top10；匹配池 Top50（行业或概念任一命中） |
| `hot_sector_pullback_ret_days` | 5 | 个股近 N 日涨幅与板块周涨幅比较 |
| `market_regime_rsi_floor` | 40 | 指数 RSI 低于此值暂停新开仓 |
| `industry_blacklist` | 银行 | 行业黑名单，命中即剔除（含包含匹配） |
| `require_min_stock_concepts` | 1 | 个股至少命中的概念标签数 |
| `max_buy_candidates` | 5 | 买入列表默认截断条数 |

---

## 一、入场（`entry`）

策略函数：`scripts/strategy_lab/strategies.py` → `trend_pullback`  
**技术面（日线）须同时满足以下 4 条：**

1. **均线多头**：`ma_8 > ma_20`
2. **趋势未破**：`收盘价 > ma_20`
3. **回踩快线**：`收盘价 < ma_8 × 1.006`（贴近 8 日线，非强势拉升段）
4. **RSI 适中**：`42 ≤ RSI_14 ≤ 72`（多头口径，见 `bull_rsi_*`）

**MACD 动能（排序加分，非入场必要条件）**

- `DIF > DEA` 且 MACD 柱较前一日放大（12/26/9）→ `macd_momentum = true`
- 命中时在 `score` 上加 **0.003**（`macd_momentum_score_bonus`），用于候选排序，不拦截买入

**第 5 条：大盘环境（买入侧开关）**

- 指数：**沪深300**（`sh000300`）、**中证1000**（`sh000852`）
- **允许新开仓**须同时满足（与信号 K 线对齐：T-1 列表用倒数第 2 根指数 bar，最新列表用倒数第 1 根）：
  - 两指数 `RSI_14` 均 **≥ 40**（任一低于 40 → 暂停买入，仅处理持仓减仓/清仓）
- 指数相对 `ma_20` 位置仅作 JSON 参考（`indices.*.above_ma`），**不再**作为买入拦截条件
- JSON 字段：`market_regime.from_previous_day_close` / `from_last_close`
- `--disable-market-regime-check` 可关闭（调试用）

**第 6 条：热门板块（买入侧过滤）**

- 数据源：`base_data` → DangInvest 板块涨幅榜 + 东方财富个股行业/概念
- 榜单：细分行业 / 概念各取近一周涨幅 Top **50** 为匹配池（`sort=change_week_desc`）；JSON 中 `industry_top` / `concept_top` 仍展示 Top **10**
- **行业或概念任一命中**匹配池即可（`industry_or_concept_in_pool`）
- **板块内回踩**（二选一即通过）：
  - 个股近 **5** 日涨幅 **<** 所匹配板块近一周涨幅（取命中板块中较高者作参考）
  - 或收盘贴近 `ma_8`：`收盘 ≤ ma_8 × 1.005`（热门板块专用更紧回踩）
- 与成本、鲁棒性、大盘环境、风格过滤一并生效后，才进入最终买入列表

热门板块快照写入 `hot_sectors`；单条候选含 `hot_sector_matched`、`hot_sector_entry_fit_passed`、`fit_reason`、`stock_ret_nd_pct` 等。

**第 7 条：风格过滤（买入侧，剔除低成长/无题材）**

- **行业黑名单**：所属行业命中 `银行`（含包含匹配，如「银行Ⅱ」「国有大型银行Ⅲ」）→ 直接剔除；**行业缺失时**概念标签命中黑名单关键词也剔除（防东财 push2 接口抖动漏拦）
- **概念标签数**：个股概念标签数须 **≥ 1**（`require_min_stock_concepts`），过滤无题材纯蓝筹
- 单条候选含 `style_filter_passed`、`style_reject_reason`、`stock_concept_count`
- 黑名单与最小概念数见 `strategy_params.INDUSTRY_BLACKLIST` / `REQUIRE_MIN_STOCK_CONCEPTS`

**买入政策：不加仓**

- **已有持仓的标的不会出现在买入列表**（`already_held: true` 在 raw 中可见，过滤后剔除）
- 本策略对持仓**只输出减仓/清仓**，不提供加仓信号

**排序分 `score`**：`(ma_8 / ma_20) - 1`，越大表示均线多头排列越强。

### stock_skill 评分（0-100）

参考 BigA 的展示方式，`daily_decisions.py` 会给每条买入候选新增 `stock_skill_score`、`rating_label`、`long_term_action`、`short_term_action`、`score_components` 与 `risk_flags`。  
注意：这是 **trend_pullback 策略信号评分**，用于排序与展示；不包含 BigA 的基本面、催化剂、热度综合判断，因此不要和 BigA 总分混用。

评分权重：

| 维度 | 权重 | 说明 |
| --- | ---: | --- |
| 趋势强度 | 25 | 原始 `score` 标准化，衡量 ma_8 / ma_20 多头强度 |
| RSI 位置 | 15 | 越接近策略理想中枢越高，超出区间为 0 |
| 鲁棒性 | 15 | `entry_consensus_ratio` 参数邻域投票 |
| 成本空间 | 15 | `edge_after_cost` 扣除往返成本后的空间 |
| 热门板块 | 15 | 行业/概念命中、板块内回踩、相对板块弱/贴近均线 |
| 风格过滤 | 5 | 行业黑名单与题材标签要求 |
| 大盘环境 | 5 | 沪深300 + 中证1000 RSI 开仓开关 |
| MACD 动能 | 5 | DIF > DEA 且柱放大 |

评级标签：

| 分数 | 标签 | 含义 |
| ---: | --- | --- |
| ≥80 | 🔥 强买候选 | 信号质量高，优先低吸 |
| 70-79 | 🔥 买入候选 | 可按回踩策略执行 |
| 60-69 | 👀 观察低吸 | 等回踩确认 |
| 50-59 | 📌 观察 | 只看不追 |
| 40-49 | ⚠️ 谨慎 | 降低仓位或放弃 |
| <40 | 🛑 回避 | 不买 |

风险项会压低最终分数：已有持仓不加仓、成本空间不足、鲁棒性不足、风格/热门板块/大盘过滤失败、昨日信号在今日已失效或触发 `exit`。

### 买入列表（两组，语义不同）

| JSON 字段 | 判断 K 线 | 说明 |
| --- | --- | --- |
| `from_previous_day_close` | 倒数第 2 根日线 | 贴近「昨日收盘出信号、今日执行」 |
| `from_last_close` | 最新一根日线 | 偏形态展示，勿与 T-1 重复计数 |

### 买入附加过滤（仅买入侧）

1. **大盘环境**：沪深300 + 中证1000 的 RSI ≥ 40
2. **热门板块**：行业或概念任一命中 Top50 池 + 板块内回踩（见上）
3. **风格过滤**：行业不在黑名单，且概念标签数 ≥ 1
4. **不加仓**：排除已有持仓标的
5. **成本过滤**：`edge_after_cost = score - (往返成本 bps / 10000)`，要求 `> 0`
6. **鲁棒性过滤**：参数邻域投票，要求 `entry_consensus_ratio ≥ 0.67`

通过全部过滤后按 `score` 降序；默认每种列表最多 **5** 只（`--max-buys 0` 不截断）。  
`--disable-hot-sector-check` / `--disable-market-regime-check` 可分别关闭对应过滤（调试用）。

---

## 二、持仓管理：趋势 / 波段双模式

持仓扫描使用 **5/10/20 日均线**（`position_reduce.py`），与入场用的 8/20 均线相互独立。  
系统会先按最新日线把持仓判成 `position_mode = trend` 或 `position_mode = swing`，每条减仓/清仓/风险提示都会带上 `mode_evidence`。

### 模式判定

**趋势模式（`trend`）** 当前满足，或最近 15 个交易日内满足过以下条件且当前仍在 `ma_20` 上方：

1. 收盘价在 `ma_20` 上方
2. 近 20 日涨幅 `ret_20 ≥ 25%`
3. `ma_20` 近 5 日斜率 `≥ 2%`
4. 量能比 `volume / volume_ma20 ≥ 1.0`，或近 20 日涨幅 `≥ 40%`

**波段模式（`swing`）**：不满足上述趋势上下文的持仓。  
若历史指标不足以判断模式，降级为趋势口径，只输出风险提示，避免因数据不完整误卖。

### 趋势模式卖出

趋势票的核心是“让利润奔跑”，因此 MA5 / MA10 / RSI 过热只提示风险，不执行正式减仓。

正式卖出只保留：

1. 破 `ma_20` → **清仓**
2. 触发 -8% 硬止损 → **清仓**

风险提示包括：

1. 买入后前 3 个交易日内破 ma_5 / ma_10 → **减仓风险提示**，不强制减仓
2. 破 ma_10 → **减仓风险提示**，不强制减仓
3. 破 ma_5 → **减仓风险提示**，不强制减仓
4. RSI 过热且仍在 ma_5 上方 → **观察提示**，可选卖出部分仓位，不强制减仓

### 波段模式卖出

波段票的核心是“盈利后更主动兑现”，触发顺序：

1. 破 `ma_20` 或 -8% 硬止损 → **清仓**
2. 买入后前 3 个交易日内破 ma_5 / ma_10 → **只提示风险**，不强制减仓
3. 破 `ma_10` → **清仓**
4. 盈利 ≥ 20%，且从持仓以来最高收盘价回撤 ≥ 8% → **减半**
5. 盈利 ≥ 15%，且跌破 `ma_5` → **减半**
6. `RSI_14 > 80` 且放量阴线 → **减半**

### 输出字段

| 字段 | 说明 |
| --- | --- |
| `position_mode` | `trend` 或 `swing` |
| `mode_reason` | `trend_context_recent` / `trend_context_matched` / `swing_context` / `insufficient_mode_data` |
| `mode_evidence.ret_20` | 近 20 日涨幅 |
| `mode_evidence.ma20_slope_5` | MA20 近 5 日斜率 |
| `mode_evidence.volume_ratio_20` | 当日量能 / 20 日均量 |
| `mode_evidence.trend_mode_recent` | 最近 15 个交易日是否出现过趋势上下文 |

---

## 三、旧版兼容：减仓与清仓

以下规则仍作为趋势模式的默认防守口径，也是指标不足时的降级口径。

### 清仓（`exit` / `action=clear`）

- **条件**：`收盘价 < ma_20`
- 与减仓优先级 1 相同；触发后列入 `sell` 与 `reduce`（`action=clear`）

> 注：`RSI > 74` 不再作为清仓或强制减仓条件；若价格仍在 `ma_5` 上方，仅输出过热观察提示。

### 减仓风险提示（`watch`）

对 `--holdings` 和/或 `--paper-account` 持仓，按**最新一根已收盘日线**判断。MA5 / MA10 / RSI 过热不再执行正式减仓，只输出风险提示。

**买入后 3 个交易日保护**：若能取得模拟盘买入日期/成本，买入成交日算第 1 个交易日，前 3 个交易日内：

- `ma_5` / `ma_10` 破位 **不触发正式减仓**，只输出 `action=watch` 的减仓风险提示。
- `收盘 < ma_20` 仍触发清仓。
- `收盘 <= 成本价 × (1 - 8%)` 仍触发硬止损清仓（`reason=hard_stop_8pct`）。

正式卖出只保留：

1. 破 ma_20 → **清仓**
2. 触发 -8% 硬止损 → **清仓**

风险提示包括：

1. 买入后前 3 个交易日内破 ma_5 / ma_10 → **减仓风险提示**，不强制减仓
2. 破 ma_10 → **减仓风险提示**，不强制减仓
3. 破 ma_5 → **减仓风险提示**，不强制减仓
4. RSI 过热且仍在 ma_5 上方 → **观察提示**，可选卖出部分仓位，不强制减仓

风险提示字段：`action: "watch"`、`reduce_ratio: 0`、`reduce_pct_label: "减仓风险提示"` 或 `"可选卖出部分"`。

| 优先级 | 触发条件 | 动作 | 状态字段 |
| --- | --- | --- | --- |
| 1 | 收盘 `<= 成本价 × 0.92` | **硬止损清仓** | 清除状态 |
| 2 | 收盘 `< ma_20` | **清仓** | 清除状态 |
| 3 | 买入后前 3 个交易日内收盘 `< ma_10` / `< ma_5` | 减仓风险提示，不卖 | 不写状态 |
| 4 | 收盘 `< ma_10` | 减仓风险提示，不卖 | 不写状态 |
| 5 | `RSI_14 > 74` 且收盘 `> ma_5` | 过热观察；提示可选卖出部分仓位 | `rsi_reduce_done` |
| 6 | 收盘 `< ma_5` 且 `RSI ≤ 74` | 减仓风险提示，不卖 | 不写状态 |

**同日冲突规则（取优先级最高的一条）：**

- 已跌破 `ma_10` → 只提示 MA10 风险，不再看 RSI / ma_5
- `RSI > 74` 时 **不触发**「跌破 ma_5」风险提示（忽略 ma_5 档位）
- RSI 过热提示（仍在 ma_5 上方）整个持仓周期 **只提示一次**；如接入模拟盘，`rsi_reduce_done` 表示“过热已提示”，不代表已卖出
- 买入日期/成本仅在接入 `--paper-account` 时可自动取得；仅传 `--holdings` 文件时无法判断保护期和硬止损成本

### 减仓状态存储（`simulated_trading`）

| 项 | 值 |
| --- | --- |
| 数据库（macOS） | `~/Library/Application Support/stock_skill_simulated_trading/stock_skill_paper_trading.db` |
| 表名 | `position_strategy_state` |
| 主键 | `(account_id, symbol, strategy)` |
| 字段 | `rsi_reduce_done`（RSI过热已提示）、`ma5_reduce_done`、`ma10_reduce_done`（兼容旧状态，当前 MA5/MA10 不再写入） |

- 买入成交 / 持仓清零 → 自动重置该标的减仓状态
- MA5/MA10 仅提示风险，不做正式减仓，避免“回踩买入后因短均线波动被动卖出”
- `--paper-account <id>` → 读持仓并写回状态
- `--no-apply-reduce-state` → 仅预览，不写库

---

## 三、风控参考（脚本不执行）

`REFERENCE_INTRADAY_STOP_PCT = 0.07`（7% 日内止损）仅作 JSON/文档参考，**不监控盘中**；需自行在交易软件设置。

---

## 环境与依赖

```bash
pip install akshare pandas numpy requests
```

同级目录需存在 `simulated_trading`（行情与模拟盘状态）；`trend_strategy/scripts/simulated_trading_path.py` 负责解析路径。

---

## 运行

```bash
SKILL_DIR="<本 skill 绝对路径>"

# 扫描买入候选
python3 "$SKILL_DIR/scripts/daily_decisions.py" --json

# 买入 + 持仓减仓/清仓（推荐：接模拟盘账户）
python3 "$SKILL_DIR/scripts/daily_decisions.py" \
  --top-n 120 \
  --paper-account alpha \
  --json

# 仅文件持仓、预览减仓（状态不持久化）
python3 "$SKILL_DIR/scripts/daily_decisions.py" \
  --holdings "$HOME/my_holdings.txt" \
  --no-apply-reduce-state \
  --json
```

### 常用参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--top-n` | 120 | 股票池大小（按成交额） |
| `--max-buys` | 5 | 买入列表截断；`0` = 不截断 |
| `--holdings` | — | 持仓文件，一行一码，`#` 注释 |
| `--paper-account` | — | 从模拟盘读持仓 + 写减仓状态 |
| `--db-path` | 用户数据目录 | 模拟盘 SQLite 路径 |
| `--no-apply-reduce-state` | false | 减仓信号预览，不写库 |
| `--roundtrip-cost-bps` | 45 | 买入往返成本（bps） |
| `--entry-consensus-min` | 0.67 | 买入鲁棒性阈值 |
| `--hot-sector-top-n` | 10 | 热门榜展示 Top N（近一周涨幅） |
| `--hot-sector-pool-n` | 50 | 行业/概念匹配池大小（任一命中即可） |
| `--disable-hot-sector-check` | false | 关闭热门板块入场过滤 |
| `--disable-market-regime-check` | false | 关闭大盘环境买入开关 |
| `--disable-robust-check` | false | 关闭鲁棒性过滤（调试用） |
| `--workers` | 10 | 拉日线并发数 |
| `--json` | false | 输出 JSON |

### JSON 输出结构（摘要）

```json
{
  "strategy": "allmarket_multi_swing_defensive",
  "market_regime": {
    "from_previous_day_close": { "allow_new_buys": true, "indices": {} },
    "from_last_close": { "allow_new_buys": true, "indices": {} }
  },
  "buy_policy": { "no_add_to_existing_holdings": true },
  "reduce_policy": { "basis": "remaining_position" },
  "buy": {
    "from_previous_day_close": [],
    "from_last_close": []
  },
  "reduce": [
    {
      "code": "600519",
      "action": "watch",
      "reduce_pct_label": "可选卖出部分",
      "reduce_basis": "remaining_position",
      "reason": "rsi_overbought_above_ma5",
      "state_before": {},
      "state_after": {},
      "state_applied": true
    }
  ],
  "sell": [],
  "params": { }
}
```

- `buy.*_raw`：过滤前原始候选；无 `_raw` 后缀为过滤后结果
- 买入候选含：`market_regime_passed`、`already_held`、`hot_sector_entry_fit_passed`、`entry_consensus_ratio` 等
- `reduce_state_note`：未接 `--paper-account` 时提示状态不会持久化

---

## 实时行情快照

```bash
python3 "$SKILL_DIR/scripts/realtime_quotes.py" 600519 000001 --json
python3 "$SKILL_DIR/scripts/realtime_quotes.py" -f "$HOME/my_holdings.txt" --intraday
```

- 数据来自 `simulated_trading` 共享 `market_data.py`（腾讯报价 + 分钟 K 聚合）
- `--intraday`：附带最后一根分钟 K（默认 `5m`）

---

## 脚本布局

| 路径 | 作用 |
| --- | --- |
| `scripts/daily_decisions.py` | 主入口：股票池扫描、买卖减仓信号 |
| `scripts/realtime_quotes.py` | 批量现价快照 |
| `scripts/simulated_trading_path.py` | 解析 `simulated_trading/scripts` 路径 |
| `scripts/base_data_path.py` | 解析 `base_data/scripts` 路径 |
| `scripts/strategy_lab/market_regime.py` | 沪深300/中证1000 大盘环境判断 |
| `scripts/strategy_lab/hot_sectors.py` | 热门板块拉取与个股匹配 |
| `scripts/strategy_lab/strategies.py` | `trend_pullback` 入场/清仓逻辑 |
| `scripts/strategy_lab/position_reduce.py` | 分档减仓逻辑（ma_5/10/20） |
| `scripts/strategy_lab/indicators.py` | 均线、RSI、MACD |
| `scripts/strategy_lab/strategy_params.py` | 默认参数 |
| `../simulated_trading/scripts/simulated_trading/market_data.py` | 行情与 `get_all_market_universe` |

---

## 与 `simulated_trading` 衔接

1. 运行本 skill → 得到 `buy` / `reduce` / `sell` 信号
2. 调用 `simulated_trading` CLI 或 HTTP API 下单（本 skill 不依赖模拟盘进程常驻）
3. 减仓「仅一次」状态由 `PaperTradingEngine` 维护于 `position_strategy_state` 表

推荐工作流：

```bash
SIM_DIR="<simulated_trading 绝对路径>"
STR_DIR="<trend_strategy 绝对路径>"

python3 "$STR_DIR/scripts/daily_decisions.py" --paper-account alpha --json
python3 "$SIM_DIR/scripts/simulated_trading_cli.py" positions alpha --json
# 按 reduce / sell 信号手动或程序化下单
```
