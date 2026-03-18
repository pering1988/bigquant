import os
import time
from pathlib import Path

import pandas as pd
import tushare as ts


# ===== 路径配置（按需修改） =====
STOCK_LIST_DIR = Path(
    "/Users/peininghe/Documents/31-python learning/Python量化策略篇-hpn/data/daily_stock_A_Financial"
)
FIN_DIR = Path(
    "/Users/peininghe/Documents/31-python learning/Python量化策略篇-hpn/data/Financial_A/FinData"
)
SHARE_DIR = Path(
    "/Users/peininghe/Documents/31-python learning/Python量化策略篇-hpn/data/Financial_A/ShareData"
)
BOARD_FILE = Path(
    "/Users/peininghe/Documents/31-python learning/Python量化策略篇-hpn/data/basic_info_A/all_BK_info_ts.csv"
)
OUT_DIR = Path(
    "/Users/peininghe/Documents/31-python learning/Python量化策略篇-hpn/data/daily_stock_A_Financial_hfq"
)

# tushare 频率限制：500 次/分钟 -> 每次调用至少间隔 0.12 秒
MIN_CALL_INTERVAL = 60 / 500


def to_ts_code(prefixed_code: str) -> str:
    """sh600000 -> 600000.SH, sz000001 -> 000001.SZ, bj920000 -> 920000.BJ"""
    c = prefixed_code.lower().replace(".csv", "")
    if c.startswith(("sh", "sz", "bj")):
        return f"{c[2:]}.{c[:2].upper()}"

    # 兜底规则：输入是纯数字代码时自动判断市场
    if c.startswith("6"):
        return f"{c}.SH"
    if c.startswith("92"):
        return f"{c}.BJ"
    return f"{c}.SZ"


def load_board_map(board_file: Path) -> dict:
    """从板块文件建立映射：{6位股票代码: {'板块代码':..., '板块名称':...}}。"""
    bk = pd.read_csv(board_file, encoding="gbk")

    required_cols = ["股票代码", "板块代码", "板块名称"]
    missing_cols = [c for c in required_cols if c not in bk.columns]
    if missing_cols:
        raise ValueError(f"板块文件缺少字段: {missing_cols}")

    bk = bk[required_cols].copy()
    bk["股票代码"] = bk["股票代码"].astype(str).str.zfill(6)
    bk = bk.drop_duplicates(subset=["股票代码"], keep="first")

    board_map = {}
    for _, row in bk.iterrows():
        board_map[row["股票代码"]] = {
            "板块代码": row["板块代码"],
            "板块名称": row["板块名称"],
        }
    return board_map


def main() -> None:
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token:
        raise RuntimeError("请先设置环境变量 TUSHARE_TOKEN")

    ts.set_token(token)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    board_map = load_board_map(BOARD_FILE)
    end_date = pd.Timestamp.today().strftime("%Y%m%d")

    # 股票列表：直接来自你给的目录文件名
    stock_list = [f.replace(".csv", "") for f in os.listdir(STOCK_LIST_DIR) if f.endswith(".csv")]
    stock_list.sort()

    last_call_time = 0.0

    for prefixed_code in stock_list:
        try:
            ts_code = to_ts_code(prefixed_code)

            # 1) 拉取后复权日线（每只股票单独 for 循环处理）
            wait = MIN_CALL_INTERVAL - (time.time() - last_call_time)
            if wait > 0:
                time.sleep(wait)
            daily = ts.pro_bar(ts_code=ts_code, adj="hfq", end_date=end_date)
            last_call_time = time.time()

            if daily is None or daily.empty:
                print(f"[SKIP] {prefixed_code}: tushare 无数据")
                continue

            # 字段中文化（保留原字段也可按需增减）
            daily = daily.rename(
                columns={
                    "ts_code": "股票代码",
                    "name": "股票名称",
                    "high": "最高价",
                    "low": "最低价",
                    "open": "开盘价",
                    "close": "收盘价",
                    "pre_close": "前收盘价",
                    "vol": "成交量",
                    "amount": "成交额",
                    "total_mv": "总市值",
                    "circ_mv": "流通市值",
                    "trade_date": "交易日期",
                }
            )
            daily["交易日期"] = pd.to_datetime(daily["交易日期"], errors="coerce")
            daily = daily.dropna(subset=["交易日期"]).sort_values("交易日期")

            # 2) 合并财务（公告日期 backward）
            fin_file = FIN_DIR / f"{prefixed_code}.csv"
            if fin_file.exists():
                fin = pd.read_csv(fin_file, encoding="gbk")
                if not fin.empty:
                    ann_col = "公告日期" if "公告日期" in fin.columns else "ann_date"
                    if ann_col in fin.columns:
                        fin[ann_col] = pd.to_datetime(fin[ann_col], errors="coerce")
                        fin = fin.dropna(subset=[ann_col]).sort_values(ann_col)
                        daily = pd.merge_asof(
                            daily,
                            fin,
                            left_on="交易日期",
                            right_on=ann_col,
                            direction="backward",
                        )

            # 3) 合并股本（CHANGE_DATE backward）
            share_file = SHARE_DIR / f"{prefixed_code}.csv"
            if share_file.exists():
                share = pd.read_csv(share_file, encoding="gbk")
                if (not share.empty) and ("CHANGE_DATE" in share.columns):
                    share["CHANGE_DATE"] = pd.to_datetime(share["CHANGE_DATE"], errors="coerce")
                    share = share.dropna(subset=["CHANGE_DATE"]).sort_values("CHANGE_DATE")
                    daily = pd.merge_asof(
                        daily,
                        share,
                        left_on="交易日期",
                        right_on="CHANGE_DATE",
                        direction="backward",
                    )

            # 4) 加板块：按股票代码直接取文件中的对应行，找不到则 NaN
            code6 = ts_code.split(".")[0]
            info = board_map.get(code6)
            if info is None:
                daily["板块代码"] = pd.NA
                daily["板块名称"] = pd.NA
            else:
                daily["板块代码"] = info["板块代码"]
                daily["板块名称"] = info["板块名称"]

            # 示例字段名对齐
            if "TOTAL_SHARES" in daily.columns:
                daily = daily.rename(columns={"TOTAL_SHARES": "total_share"})
            if "UNLIMITED_SHARES" in daily.columns:
                daily = daily.rename(columns={"UNLIMITED_SHARES": "float_share"})

            # 4.1) 计算总市值/流通市值：收盘价 * 股本数
            if "收盘价" in daily.columns and "close" in daily.columns:
                # 兼容前面字段重命名失败或未改名的情况
                daily["收盘价"] = daily["close"]

            if "收盘价" in daily.columns:
                close_num = pd.to_numeric(daily["收盘价"], errors="coerce")

                if "total_share" in daily.columns:
                    total_share_num = pd.to_numeric(daily["total_share"], errors="coerce")
                    daily["总市值"] = close_num * total_share_num

                if "float_share" in daily.columns:
                    float_share_num = pd.to_numeric(daily["float_share"], errors="coerce")
                    daily["流通市值"] = close_num * float_share_num

            # 5) 输出
            out_file = OUT_DIR / f"{prefixed_code}.csv"
            daily.to_csv(out_file, index=False, encoding="utf-8-sig")
            print(f"[OK] {prefixed_code} -> {out_file}")

        except Exception as e:
            print(f"[ERROR] {prefixed_code}: {e}")


if __name__ == "__main__":
    main()
