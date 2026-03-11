---
name: openclaw-southbound-weekly
description: 面向“输入文件仅包含股票代码列表”的周度分析技能：读取 south_stocklist 目录最新日期后缀文件并提取起始日期，调用已配置大模型完成基本面/技术面/当周资金流入分析与价格检索，生成建议配比与15万元等额配比收益对比，并输出汇总文档（可上传飞书）。
---

# OpenClaw 南向资金周度分析（简化版）

## Overview

本技能假设输入文件只有股票代码，不依赖本地行情与财务字段；关键分析与价格检索由已配置大模型完成。

## Workflow

1. 读取 `/home/admin/.openclaw/workspace/stockdata/south_stocklist` 下最新日期文件。
2. 使用文件名 `YYYYMMDD` 作为周期起始日。
3. 读取股票代码列表（txt/csv）。
4. 调用 `skills/openclaw-southbound-weekly/agents/openai.yaml` 中配置的大模型，生成：
   - 基本面、技术面、资金流分析
   - 建议配比（权重）
   - 收益计算所需价格（周一开盘 + 周五收盘/最新收盘）
5. 本地汇总并计算两种策略收益：建议配比 vs 15万元等额配比。
6. 输出 `summary.md`、`top10_selection.csv`、`allocation_and_pnl.csv`，可选上传飞书。

## Quick Start

```bash
export DASHSCOPE_API_KEY="<your_key>"
python skills/openclaw-southbound-weekly/scripts/run_analysis.py \
  --input-dir /home/admin/.openclaw/workspace/stockdata/south_stocklist \
  --agent-yaml skills/openclaw-southbound-weekly/agents/openai.yaml \
  --capital 150000
```

仅本地输出不上传飞书：

```bash
python skills/openclaw-southbound-weekly/scripts/run_analysis.py --skip-feishu-upload
```
