# A股后复权行情合并脚本（简化版）

脚本：`stock_data_builder.py`

## 处理逻辑（按股票逐只 for 循环）
1. 从 `daily_stock_A_Financial` 目录读取股票列表（文件名如 `sh600000.csv`）。
2. 每只股票调用 `ts.pro_bar(..., adj='hfq')` 拉全量后复权日线。
3. 每只股票读取本地财务文件，用 `公告日期/ann_date` 与 `交易日期` 做 `merge_asof(direction='backward')`。
4. 每只股票读取本地股本文件，用 `CHANGE_DATE` 与 `交易日期` 做 `merge_asof(direction='backward')`。
5. 板块信息直接从 `all_BK_info_ts.csv` 按股票代码匹配对应行，取 `板块代码`、`板块名称`；若找不到则写入 `pd.NA`。
6. 用每行`收盘价`分别乘以 `total_share` 与 `float_share`，得到 `总市值`、`流通市值`。
7. 每只股票输出一个 CSV 到 `daily_stock_A_Financial_hfq`。

## 文件编码
- 读取本地 CSV 使用 `encoding="gbk"`（板块、财务、股本文件）。

## 接口频率限制
- 脚本内置了 `500 次/分钟` 的限速控制（每次请求间隔至少约 `0.12` 秒），避免触发 Tushare 频率限制。

## 运行
```bash
pip install pandas tushare
export TUSHARE_TOKEN='你的token'
python stock_data_builder.py
```

## 路径
脚本顶部常量可直接修改：
- `STOCK_LIST_DIR`
- `FIN_DIR`
- `SHARE_DIR`
- `BOARD_FILE`
- `OUT_DIR`
