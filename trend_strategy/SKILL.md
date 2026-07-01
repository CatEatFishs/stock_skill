---
name: a-share-strategy-allmarket-multi-swing-defensive
description: A 股全市场高流动性池内按趋势回踩（trend_pullback）产出买入、分档减仓与清仓信号，覆盖主板/创业板/科创板；含 MACD 金叉入场与模拟盘减仓状态持久化。Use when 用户要选股、判断买卖/减仓信号、看实时报价或检查持仓离场条件，且不排除创业板和科创板。
---

# 全市场多波段防御型 · 决策信号

基于日线 `trend_pullback`（趋势回踩）策略，从全市场高流动性股票池中扫描**买入候选**，并对持仓输出**分档减仓**与**清仓**参考。  
输出为结构化 JSON / 终端列表，**不跑回测、不自动下单**；执行请接 `simulated_trading` 或券商通道。

## 能力边界

| 做 | 不做 |
| --- | --- |
| 全市场（含创业板/科创板）高流动性股票池扫描 | 分钟级回测、收益曲线 |
| 日线入场 `entry`、清仓 `exit`、分档减仓 `reduce` | 保证收益、替代投顾 |
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
| `macd_fast/slow/signal` | 12 / 26 / 9 | 日 K MACD |
| `exit_rsi` | 74 | 减仓：RSI 过热阈值 |
| `roundtrip_cost_bps` | 45 | 买入成本过滤 |
| `entry_consensus_min` | 0.67 | 买入鲁棒性过滤 |
| `hot_sector_top_n` / `hot_sector_pool_n` | 10 / 30 | 展示 Top10；匹配池 Top30（行业与概念均需命中） |
| `hot_sector_pullback_ret_days` | 5 | 个股近 N 日涨幅与板块周涨幅比较 |
| `market_regime_rsi_floor` | 40 | 指数 RSI 低于此值暂停新开仓 |
| `max_buy_candidates` | 5 | 买入列表默认截断条数 |

---

## 一、入场（`entry`）

策略函数：`scripts/strategy_lab/strategies.py` → `trend_pullback`  
**技术面（日线）须同时满足以下 5 条：**

1. **均线多头**：`ma_8 > ma_20`
2. **趋势未破**：`收盘价 > ma_20`
3. **回踩快线**：`收盘价 < ma_8 × 1.006`（贴近 8 日线，非强势拉升段）
4. **RSI 适中**：`42 ≤ RSI_14 ≤ 72`（多头口径，见 `bull_rsi_*`）
5. **日 K MACD 动能**：`DIF > DEA` 且 **MACD 柱较前一日放大**（12/26/9）

**第 6 条：大盘环境（买入侧开关）**

- 指数：**沪深300**（`000300`）、**中证1000**（`000852`）
- **允许新开仓**须同时满足（与信号 K 线对齐：T-1 列表用倒数第 2 根指数 bar，最新列表用倒数第 1 根）：
  - 两指数收盘均在 `ma_20` 上方
  - 两指数 `RSI_14` 均 **≥ 40**（任一低于 40 → 暂停买入，仅处理持仓减仓/清仓）
- JSON 字段：`market_regime.from_previous_day_close` / `from_last_close`
- `--disable-market-regime-check` 可关闭（调试用）

**第 7 条：热门板块（买入侧过滤）**

- 数据源：`base_data` → DangInvest 板块涨幅榜 + 东方财富个股行业/概念
- 榜单：细分行业 / 概念各取近一周涨幅 Top **30** 为匹配池（`sort=change_week_desc`）；JSON 中 `industry_top` / `concept_top` 仍展示 Top **10**
- **行业与概念须同时命中**匹配池（不再「任一命中」），减少纯概念蹭热点
- **板块内回踩**（二选一即通过）：
  - 个股近 **5** 日涨幅 **<** 所匹配板块近一周涨幅（取命中板块中较高者作参考）
  - 或收盘贴近 `ma_8`：`收盘 ≤ ma_8 × 1.005`（热门板块专用更紧回踩）
- 与成本、鲁棒性、大盘环境过滤一并生效后，才进入最终买入列表

热门板块快照写入 `hot_sectors`；单条候选含 `hot_sector_matched`、`hot_sector_entry_fit_passed`、`fit_reason`、`stock_ret_nd_pct` 等。

**买入政策：不加仓**

- **已有持仓的标的不会出现在买入列表**（`already_held: true` 在 raw 中可见，过滤后剔除）
- 本策略对持仓**只输出减仓/清仓**，不提供加仓信号

**排序分 `score`**：`(ma_8 / ma_20) - 1`，越大表示均线多头排列越强。

### 买入列表（两组，语义不同）

| JSON 字段 | 判断 K 线 | 说明 |
| --- | --- | --- |
| `from_previous_day_close` | 倒数第 2 根日线 | 贴近「昨日收盘出信号、今日执行」 |
| `from_last_close` | 最新一根日线 | 偏形态展示，勿与 T-1 重复计数 |

### 买入附加过滤（仅买入侧）

1. **大盘环境**：沪深300 + 中证1000 均在 `ma_20` 上且 RSI ≥ 40
2. **热门板块**：行业与概念同时命中 Top30 池 + 板块内回踩（见上）
3. **不加仓**：排除已有持仓标的
4. **成本过滤**：`edge_after_cost = score - (往返成本 bps / 10000)`，要求 `> 0`
5. **鲁棒性过滤**：参数邻域投票，要求 `entry_consensus_ratio ≥ 0.67`

通过全部过滤后按 `score` 降序；默认每种列表最多 **5** 只（`--max-buys 0` 不截断）。  
`--disable-hot-sector-check` / `--disable-market-regime-check` 可分别关闭对应过滤（调试用）。

---

## 二、持仓管理：减仓与清仓

持仓扫描使用 **5/10/20 日均线**（`position_reduce.py`），与入场用的 8/20 均线相互独立。

### 清仓（`exit` / `action=clear`）

- **条件**：`收盘价 < ma_20`
- 与减仓优先级 1 相同；触发后列入 `sell` 与 `reduce`（`action=clear`）

> 注：原「RSI > 74 直接清仓」已改为下方「减仓 1/3」，不再作为 `exit` 条件。

### 分档减仓（`reduce`）

对 `--holdings` 和/或 `--paper-account` 持仓，按**最新一根已收盘日线**判断。**每个档位仅触发一次**（需 `--paper-account` 写库）。

**减仓基数**：各档 `reduce_ratio`（1/3、1/2）均按**触发时剩余仓位**计算，非初始总仓位。例如：

1. RSI 过热减 1/3 → 剩 **2/3**
2. 破 ma_5 再减 1/3 → 约剩 **4/9**
3. 破 ma_10 减 1/2 → 约剩 **2/9**
4. 破 ma_20 → **清仓**

信号字段：`reduce_basis: "remaining_position"`。

| 优先级 | 触发条件 | 动作 | 状态字段 |
| --- | --- | --- | --- |
| 1 | 收盘 `< ma_20` | **清仓** | 清除状态 |
| 2 | 收盘 `< ma_10` | 减仓 **1/2** | `ma10_reduce_done` |
| 3 | `RSI_14 > 74` 且收盘 `> ma_5` | 减仓 **1/3** | `rsi_reduce_done` |
| 4 | 收盘 `< ma_5` 且 `RSI ≤ 74` | 减仓 **1/3** | `ma5_reduce_done` |

**同日冲突规则（取优先级最高的一条）：**

- 已跌破 `ma_10` → 执行减半，不再看 RSI / ma_5
- `RSI > 74` 时 **不触发**「跌破 ma_5」减仓（忽略 ma_5 档位）
- RSI 过热减仓（仍在 ma_5 上方）整个持仓周期 **只减一次**

### 减仓状态存储（`simulated_trading`）

| 项 | 值 |
| --- | --- |
| 数据库（macOS） | `~/Library/Application Support/simulated_trading/paper_trading.db` |
| 表名 | `position_strategy_state` |
| 主键 | `(account_id, symbol, strategy)` |
| 字段 | `rsi_reduce_done`、`ma5_reduce_done`、`ma10_reduce_done` |

- 买入成交 / 持仓清零 → 自动重置该标的减仓状态
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
| `--hot-sector-pool-n` | 30 | 行业/概念匹配池大小（须同时命中） |
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
      "action": "reduce",
      "reduce_pct_label": "1/3",
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
