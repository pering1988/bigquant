# 数据契约（最新）

## 输入

- 文件路径：`/home/admin/.openclaw/workspace/stockdata/south_stocklist`
- 文件名：需包含 `YYYYMMDD`
- 文件内容：仅股票代码（每行一个，或csv第一列）

## 分析来源

- 文本分析（基本面/技术面/资金流）：系统默认大模型
- 价格数据（收益计算）：AKShare
  - 港股 `stock_hk_hist`
  - A股 `stock_zh_a_hist`

## 输出

- 脚本默认不落地本地文件
- 仅生成文档内容，并询问是否创建飞书文档写入
