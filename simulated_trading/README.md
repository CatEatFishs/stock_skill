# simulated_trading

A 股模拟盘交易与回测 skill：账户、限价/市价撮合、持仓、订单、净值快照与简单回测。

## 数据目录

默认 SQLite 与用户数据目录（不在 skill 目录内）：

- macOS: `~/Library/Application Support/simulated_trading/`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/simulated_trading/`

默认数据库：

- `~/Library/Application Support/simulated_trading/paper_trading.db`（macOS）

若曾使用旧目录 `a-share-paper-trading`，在未创建新目录前会自动沿用旧路径。

环境变量（可选）：

- `SIMULATED_TRADING_HOME`：自定义数据根目录
- `A_SHARE_PAPER_TRADING_HOME`：旧名，仍兼容

## 快速开始

```bash
SKILL_DIR="<本仓库 simulated_trading 绝对路径>"
python3 "$SKILL_DIR/scripts/simulated_trading_ctl.py" start
python3 "$SKILL_DIR/scripts/simulated_trading_cli.py" create-account alpha --cash 1000000
python3 "$SKILL_DIR/scripts/simulated_trading_cli.py" positions alpha --json
```

详细说明见 `SKILL.md`。
