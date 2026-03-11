---
name: openclaw-southbound-weekly
description: 读取南向资金股票池目录中“文件名后缀日期”最新的文件（该文件已是最近一周南向资金流入最多Top2行业的Top10股票），提取后缀日期为周期起始日，结合基本面/技术面/资金面生成未来一周走势判断与建议买入配比，并对比“建议配比策略”与“15万元等额策略”在周一开盘买入到周五收盘（或当前最新收盘）卖出的收益，同时将汇总文档上传到飞书云文档。
---

# OpenClaw 南向资金周度组合分析

## Overview

使用本技能完成“读取最新周文件 -> 10只股票打分配比 -> 双策略收益对比 -> 汇总文档上传飞书云文档”的一体化流程。

## Workflow

1. 定位最新文件：在 `/home/admin/.openclaw/workspace/stockdata/south_stocklist` 中读取文件名包含 `YYYYMMDD` 的最新文件。
2. 解析起始日期：将最新文件名中的日期直接作为本周期 `start_date`。
3. 股票范围：默认输入文件已经是“最近一周南向流入最多Top2行业的Top10股票”，不再全市场二次筛选。
4. 组合分析：结合基本面/技术面/资金面计算综合分与建议买入配比，按15万元分配资金。
5. 收益测算：
   - 策略A：建议配比，周一开盘买入，周五收盘卖出（未到周五则最新收盘）。
   - 策略B：15万元等额买入，规则同上。
6. 对比与归档：输出 `summary.md`，并上传到飞书云盘（云文档）。

## Quick Start

```bash
python skills/openclaw-southbound-weekly/scripts/run_analysis.py \
  --input-dir /home/admin/.openclaw/workspace/stockdata/south_stocklist \
  --capital 150000 \
  --output-dir skills/openclaw-southbound-weekly/output \
  --feishu-app-id "$FEISHU_APP_ID" \
  --feishu-app-secret "$FEISHU_APP_SECRET" \
  --feishu-folder-token "$FEISHU_FOLDER_TOKEN"
```

仅本地生成不上传飞书：

```bash
python skills/openclaw-southbound-weekly/scripts/run_analysis.py --skip-feishu-upload
```

## Output

- `top10_selection.csv`：10只股票打分与建议权重
- `allocation_and_pnl.csv`：建议配比 vs 等额配比的收益明细
- `summary.md`：汇总文档（含收益对比结论）
- 终端打印飞书上传结果（URL 或错误原因）

## Notes

- 飞书上传使用 `drive/v1/files/upload_all`，需要应用具备云空间写入权限。
- 支持通过环境变量传参：`FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_FOLDER_TOKEN`。
- 列名兼容策略见 `references/data_contract.md`。
