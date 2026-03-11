---
name: openclaw-southbound-weekly
description: 面向“仅股票代码列表输入”的周度分析技能：读取最新日期文件后，使用系统默认大模型完成基本面/技术面/资金流分析，使用 AKShare 获取港股与A股价格计算收益，最终仅生成文档内容并询问是否创建飞书文档写入结果。
---

# OpenClaw 南向资金周度分析（AKShare + 系统默认模型）

## Workflow

1. 读取 `/home/admin/.openclaw/workspace/stockdata/south_stocklist` 中最新日期后缀文件。
2. 解析文件中的股票代码列表（仅代码）。
3. 调用系统默认大模型，输出每只股票的基本面/技术面/资金流分析与建议配比。
4. 调用 AKShare 获取价格：
   - 港股：`ak.stock_hk_hist(symbol="00593", period="daily", start_date="19700101", end_date="22220101", adjust="qfq")`
   - A股：`ak.stock_zh_a_hist(symbol="000001", period="daily", start_date="20170301", end_date="20240528", adjust="")`
5. 计算建议配比 vs 15万元等额配比收益。
6. 不写本地文件，仅生成文档内容并询问是否创建飞书文档写入。

## Quick Start

```bash
export SYSTEM_LLM_BASE_URL="<openai_compatible_endpoint>"
export SYSTEM_LLM_API_KEY="<api_key>"
export SYSTEM_LLM_MODEL="<default_model_name>"
export FEISHU_APP_ID="<app_id>"
export FEISHU_APP_SECRET="<app_secret>"

python skills/openclaw-southbound-weekly/scripts/run_analysis.py \
  --input-dir /home/admin/.openclaw/workspace/stockdata/south_stocklist
```

自动确认创建飞书文档：

```bash
python skills/openclaw-southbound-weekly/scripts/run_analysis.py --yes
```
