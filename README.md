# stock_skill

股票 · A 股数据查询 Skill 集合。

## 交易时间口径

自 2026-07-06 起，盘后固定价格交易适用品种扩展至全部 A 股及 ETF。本仓库文档统一区分两个时间点：普通竞价收盘价在 `15:00` 形成；模拟盘“业务日结束 / 当日单过期”按盘后固定价格交易结束后的 `15:30` 处理。

## Skills

- `base_data`：查询 A 股实时行情、历史数据、技术指标、事件、资金面、热门行业/概念、板块热力图与个股行业信息。
- `trend_strategy`：全市场趋势回踩选股与买卖/减仓信号。
- `simulated_trading`：A 股模拟盘交易、账户与撮合。

## 安装

```bash
cp -R base_data trend_strategy simulated_trading ~/.cursor/skills/
```

详细用法见各目录下的 `SKILL.md`。
