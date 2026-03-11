# 数据契约（列名兼容）

输入文件默认为：**最近一周南向资金流入最多Top2行业的前10只股票**。脚本采用候选列名自动匹配。

## 必需维度

- 股票代码：`code` / `stock_code` / `symbol`
- 股票名称：`name` / `stock_name`
- 行业：`industry` / `industry_name` / `sector`

## 建议提供（用于行业聚合展示）

- 南向净流入：`south_net_inflow` / `southbound_net_inflow` / `net_inflow`

## 基本面候选列

- `pe`, `pb`, `roe`, `revenue_yoy`, `profit_yoy`

## 技术面候选列

- `rsi`, `ma5`, `ma20`, `close`, `macd`

## 资金面增强候选列

- `main_net_inflow`, `volume_ratio`, `turnover_rate`

## 收益测算价格列（优先级从左到右）

- 周一开盘：`monday_open` / `open_monday` / `week_open` / `open`
- 周五收盘：`friday_close` / `close_friday` / `week_close`
- 最新收盘：`latest_close` / `current_close` / `close`

若执行时未到周五或无周五收盘列，脚本使用“最新收盘”作为卖出价。

## 飞书上传参数

- `FEISHU_APP_ID`
- `FEISHU_APP_SECRET`
- `FEISHU_FOLDER_TOKEN`

或通过命令行参数显式传入：
`--feishu-app-id` / `--feishu-app-secret` / `--feishu-folder-token`
