# 数据契约（简化版）

## 输入文件

- 目录：`/home/admin/.openclaw/workspace/stockdata/south_stocklist`
- 文件名：需包含 `YYYYMMDD` 后缀日期
- 内容：仅股票代码列表（txt/csv）

### txt 示例

```text
00700.HK
00941.HK
03690.HK
```

### csv 示例

```csv
code
00700.HK
00941.HK
03690.HK
```

## 大模型返回结构

脚本要求模型返回 JSON，核心字段：

- `stocks[]`
  - `code`
  - `name`
  - `industry`
  - `fundamental_analysis`
  - `technical_analysis`
  - `capital_flow_analysis`
  - `trend_next_week`
  - `suggest_weight`
  - `monday_open`
  - `sell_price`

## 输出文件

- `top10_selection.csv`
- `allocation_and_pnl.csv`
- `summary.md`
