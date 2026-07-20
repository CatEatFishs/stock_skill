# simulated_trading

A 股模拟盘交易与回测 skill：账户、限价/市价撮合、持仓、订单、净值快照与简单回测。

## 交易时间口径

自 2026-07-06 起，盘后固定价格交易适用品种扩展至全部 A 股及 ETF。文档中“收盘后”“当日单过期”统一按盘后固定价格交易结束后的 `15:30` 处理；普通竞价收盘价仍在 `15:00` 形成，日线策略信号仍以该收盘价为准。

## 数据目录

默认 SQLite 与用户数据目录（不在 skill 目录内）：

- macOS: `~/Library/Application Support/stock_skill_simulated_trading/`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/stock_skill_simulated_trading/`

默认数据库：

- `~/Library/Application Support/stock_skill_simulated_trading/stock_skill_paper_trading.db`（macOS）

本仓库不再自动沿用旧目录 `a-share-paper-trading`，以避免和 a-share-skill 的模拟盘共用账户库。

环境变量（可选）：

- `STOCK_SKILL_SIMULATED_TRADING_HOME`：stock_skill 专用数据根目录
- `SIMULATED_TRADING_HOME`：通用自定义数据根目录

## 快速开始

```bash
SKILL_DIR="<本仓库 simulated_trading 绝对路径>"
python3 "$SKILL_DIR/scripts/simulated_trading_ctl.py" start
python3 "$SKILL_DIR/scripts/simulated_trading_cli.py" create-account alpha --cash 1000000
python3 "$SKILL_DIR/scripts/simulated_trading_cli.py" positions alpha --json
```

详细说明见 `SKILL.md`。
