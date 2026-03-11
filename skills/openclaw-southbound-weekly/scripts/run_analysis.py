#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pandas as pd

DATE_RE = re.compile(r"(20\d{6})")


def pick_col(df: pd.DataFrame, candidates):
    """从候选列名列表中返回第一个命中的列名。"""
    for c in candidates:
        if c in df.columns:
            return c
    return None


def latest_file(input_dir: Path) -> tuple[Path, dt.date]:
    """扫描目录并返回“文件名包含YYYYMMDD日期”的最新文件及其日期。"""
    files = [p for p in input_dir.iterdir() if p.is_file()]
    dated = []
    for f in files:
        m = DATE_RE.search(f.stem) or DATE_RE.search(f.name)
        if not m:
            continue
        d = dt.datetime.strptime(m.group(1), "%Y%m%d").date()
        dated.append((d, f))
    if not dated:
        raise FileNotFoundError("未找到包含 YYYYMMDD 日期后缀的文件")
    d, f = sorted(dated, key=lambda x: x[0])[-1]
    return f, d


def read_any(path: Path) -> pd.DataFrame:
    """按后缀读取常见表格文件。"""
    suf = path.suffix.lower()
    if suf in {".csv", ".txt"}:
        return pd.read_csv(path)
    if suf in {".xlsx", ".xls"}:
        return pd.read_excel(path)
    if suf == ".parquet":
        return pd.read_parquet(path)
    raise ValueError(f"不支持的文件类型: {suf}")


def zscore(s):
    """计算标准分，遇到样本不足或标准差为0时返回全0序列。"""
    s = pd.to_numeric(s, errors="coerce")
    if s.notna().sum() < 2:
        return pd.Series([0.0] * len(s), index=s.index)
    std = s.std(ddof=0)
    if std == 0 or pd.isna(std):
        return pd.Series([0.0] * len(s), index=s.index)
    return (s - s.mean()) / std


def compute_scores(df: pd.DataFrame) -> pd.DataFrame:
    """基于基本面/技术面/资金面计算综合分与建议权重。"""
    # ---------- 基本面得分 ----------
    pe = pick_col(df, ["pe"])
    pb = pick_col(df, ["pb"])
    roe = pick_col(df, ["roe"])
    rev = pick_col(df, ["revenue_yoy"])
    profit = pick_col(df, ["profit_yoy"])

    f_score = pd.Series(0.0, index=df.index)
    if pe:
        f_score += -zscore(df[pe]) * 0.20
    if pb:
        f_score += -zscore(df[pb]) * 0.15
    if roe:
        f_score += zscore(df[roe]) * 0.30
    if rev:
        f_score += zscore(df[rev]) * 0.20
    if profit:
        f_score += zscore(df[profit]) * 0.15

    # ---------- 技术面得分 ----------
    rsi = pick_col(df, ["rsi"])
    ma5 = pick_col(df, ["ma5"])
    ma20 = pick_col(df, ["ma20"])
    close = pick_col(df, ["close"])
    macd = pick_col(df, ["macd"])

    t_score = pd.Series(0.0, index=df.index)
    if rsi:
        r = pd.to_numeric(df[rsi], errors="coerce")
        t_score += (-(r - 55).abs() / 100).fillna(0) * 0.25
    if ma5 and ma20:
        t_score += zscore(pd.to_numeric(df[ma5], errors="coerce") - pd.to_numeric(df[ma20], errors="coerce")) * 0.45
    elif close and ma20:
        t_score += zscore(pd.to_numeric(df[close], errors="coerce") - pd.to_numeric(df[ma20], errors="coerce")) * 0.45
    if macd:
        t_score += zscore(df[macd]) * 0.30

    # ---------- 资金面得分 ----------
    south = pick_col(df, ["south_net_inflow", "southbound_net_inflow", "net_inflow"])
    main = pick_col(df, ["main_net_inflow"])
    vol = pick_col(df, ["volume_ratio"])
    turn = pick_col(df, ["turnover_rate"])

    c_score = pd.Series(0.0, index=df.index)
    if south:
        c_score += zscore(df[south]) * 0.60
    if main:
        c_score += zscore(df[main]) * 0.25
    if vol:
        c_score += zscore(df[vol]) * 0.10
    if turn:
        c_score += zscore(df[turn]) * 0.05

    # 综合分：基本面35% + 技术面25% + 资金面40%
    out = df.copy()
    out["fundamental_score"] = f_score
    out["technical_score"] = t_score
    out["capital_score"] = c_score
    out["total_score"] = 0.35 * f_score + 0.25 * t_score + 0.40 * c_score

    # 仅对正分部分归一化分配权重；若全部<=0，则改为等权
    positive = out["total_score"].clip(lower=0)
    out["suggest_weight"] = (positive / positive.sum()) if positive.sum() else (1 / len(out))
    return out


def calc_pnl(df: pd.DataFrame, capital: float):
    """计算建议配比与等额配比两种策略的收益。"""
    # 兼容多种价格字段命名
    monday_col = pick_col(df, ["monday_open", "open_monday", "week_open", "open"])
    friday_col = pick_col(df, ["friday_close", "close_friday", "week_close"])
    latest_close_col = pick_col(df, ["latest_close", "current_close", "close"])
    if not monday_col:
        raise ValueError("缺少周一开盘价列（monday_open/open_monday/week_open/open）")

    # 周五前（周一~周四）默认使用最新收盘作为卖出价
    use_latest = dt.datetime.now().weekday() < 4
    sell_col = latest_close_col if use_latest or not friday_col else friday_col
    if not sell_col:
        raise ValueError("缺少卖出价格列（friday_close/close_friday/week_close/latest_close/current_close/close）")

    # 收益率 = 卖出价 / 买入价 - 1
    d = df.copy()
    d["buy_price"] = pd.to_numeric(d[monday_col], errors="coerce")
    d["sell_price"] = pd.to_numeric(d[sell_col], errors="coerce")
    d["ret"] = d["sell_price"] / d["buy_price"] - 1

    # 策略A：按模型建议权重分配资金
    d["amount_suggest"] = capital * d["suggest_weight"]
    d["pnl_suggest"] = d["amount_suggest"] * d["ret"]

    # 策略B：总资金等额分配
    d["amount_equal"] = capital / len(d)
    d["pnl_equal"] = d["amount_equal"] * d["ret"]
    return d, use_latest


def _request_json(url: str, method: str = "GET", headers: dict | None = None, data: bytes | None = None):
    """发送HTTP请求并将响应解析为JSON。"""
    req = urllib.request.Request(url=url, method=method, headers=headers or {}, data=data)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


def get_feishu_token(app_id: str, app_secret: str) -> str:
    """调用飞书开放平台接口获取 tenant_access_token。"""
    payload = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8")
    rsp = _request_json(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
        data=payload,
    )
    if rsp.get("code") != 0:
        raise RuntimeError(f"获取飞书token失败: {rsp}")
    return rsp["tenant_access_token"]


def upload_to_feishu_drive(file_path: Path, app_id: str, app_secret: str, folder_token: str) -> str:
    """上传本地文件到飞书云盘目录，返回URL或文件token。"""
    token = get_feishu_token(app_id, app_secret)
    file_bytes = file_path.read_bytes()
    boundary = f"----codex-{uuid.uuid4().hex}"

    fields = {
        "file_name": file_path.name,
        "parent_type": "explorer",
        "parent_node": folder_token,
        "size": str(len(file_bytes)),
    }

    # 采用 multipart/form-data 手工拼接上传体
    chunks = []
    for k, v in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())

    chunks.append(f"--{boundary}\r\n".encode())
    chunks.append(
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\nContent-Type: text/markdown\r\n\r\n'.encode()
    )
    chunks.append(file_bytes)
    chunks.append("\r\n".encode())
    chunks.append(f"--{boundary}--\r\n".encode())

    body = b"".join(chunks)
    rsp = _request_json(
        "https://open.feishu.cn/open-apis/drive/v1/files/upload_all",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        data=body,
    )
    if rsp.get("code") != 0:
        raise RuntimeError(f"上传飞书云文档失败: {rsp}")
    data = rsp.get("data", {})
    return data.get("url") or data.get("file_token", "")


def build_dataset(raw: pd.DataFrame) -> pd.DataFrame:
    """校验输入结构并整理分析样本（最多保留10只）。"""
    code_col = pick_col(raw, ["code", "stock_code", "symbol"])
    name_col = pick_col(raw, ["name", "stock_name"])
    ind_col = pick_col(raw, ["industry", "industry_name", "sector"])
    inflow_col = pick_col(raw, ["south_net_inflow", "southbound_net_inflow", "net_inflow"])

    for k, v in {"code": code_col, "name": name_col, "industry": ind_col}.items():
        if not v:
            raise ValueError(f"缺少必要字段: {k}")

    d = raw.copy()
    if inflow_col:
        d[inflow_col] = pd.to_numeric(d[inflow_col], errors="coerce")

    # 用户指定：文件本身就是“最近一周南向流入最多Top2行业的Top10股票”。
    # 因此不再按全市场重算，仅在行数>10时按南向净流入降序截取前10。
    if len(d) > 10 and inflow_col:
        d = d.sort_values(inflow_col, ascending=False).head(10).copy()
    elif len(d) > 10:
        d = d.head(10).copy()

    return d


def main():
    """命令行入口：执行选股分析、收益对比与飞书上传。"""
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", default="/home/admin/.openclaw/workspace/stockdata/south_stocklist")
    ap.add_argument("--capital", type=float, default=150000)
    ap.add_argument("--output-dir", default="./output")
    ap.add_argument("--feishu-app-id", default=os.getenv("FEISHU_APP_ID"))
    ap.add_argument("--feishu-app-secret", default=os.getenv("FEISHU_APP_SECRET"))
    ap.add_argument("--feishu-folder-token", default=os.getenv("FEISHU_FOLDER_TOKEN"))
    ap.add_argument("--skip-feishu-upload", action="store_true")
    args = ap.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1) 读取最新一期文件，并提取文件后缀日期作为周期起点
    latest, start_date = latest_file(Path(args.input_dir))
    raw = read_any(latest)
    data = build_dataset(raw)

    code_col = pick_col(data, ["code", "stock_code", "symbol"])
    name_col = pick_col(data, ["name", "stock_name"])
    ind_col = pick_col(data, ["industry", "industry_name", "sector"])
    inflow_col = pick_col(data, ["south_net_inflow", "southbound_net_inflow", "net_inflow"])

    # 2) 从文件内聚合展示Top2行业（文件已预筛为Top10股票）
    top2 = []
    if inflow_col and ind_col:
        top2 = (
            data.groupby(ind_col, as_index=False)[inflow_col]
            .sum()
            .sort_values(inflow_col, ascending=False)
            .head(2)[ind_col]
            .tolist()
        )

    # 3) 计算评分与两种策略收益
    scored = compute_scores(data)
    pnl_df, use_latest = calc_pnl(scored, args.capital)

    selection_cols = [code_col, name_col, ind_col, "fundamental_score", "technical_score", "capital_score", "total_score", "suggest_weight"]
    if inflow_col:
        selection_cols.insert(3, inflow_col)
    pnl_cols = [code_col, name_col, "buy_price", "sell_price", "ret", "amount_suggest", "pnl_suggest", "amount_equal", "pnl_equal"]

    # 4) 输出中间结果表
    selection = pnl_df[selection_cols].copy()
    pnl_out = pnl_df[pnl_cols].copy()
    selection.to_csv(output_dir / "top10_selection.csv", index=False)
    pnl_out.to_csv(output_dir / "allocation_and_pnl.csv", index=False)

    weighted_pnl = pnl_out["pnl_suggest"].sum()
    equal_pnl = pnl_out["pnl_equal"].sum()
    excess = weighted_pnl - equal_pnl
    sell_rule = "最新收盘" if use_latest else "周五收盘"

    # 5) 生成汇总文档
    summary = f"""# 南向资金周度组合汇总

- 周期起始日（来自最新文件后缀日期）：{start_date}
- 数据文件：{latest}
- 执行时间：{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 一、行业与股票范围

- 输入文件已预筛选为“最近一周南向资金流入最多Top2行业的Top10股票”
- 行业（按文件聚合）：{', '.join(map(str, top2)) if top2 else '未提供南向净流入字段，无法聚合行业'}
- 本次纳入股票数量：{len(selection)}

## 二、未来一周走势分析与建议配比

- 分析维度：基本面（35%）+技术面（25%）+资金面（40%）
- 总资金：{args.capital:,.2f} 元
- 每只股票建议买入配比与金额见 `allocation_and_pnl.csv` 中 `amount_suggest`

## 三、收益测算

- 卖出规则：{sell_rule}
- 策略A（建议配比）总收益：{weighted_pnl:,.2f} 元
- 策略B（等额配比）总收益：{equal_pnl:,.2f} 元

## 四、对比结论

- 超额收益（A-B）：{excess:,.2f} 元
- {'建议采用策略A。' if excess > 0 else '建议采用策略B或继续优化打分权重。'}
"""

    summary_path = output_dir / "summary.md"
    summary_path.write_text(summary, encoding="utf-8")

    # 6) 视配置决定是否上传飞书云文档
    feishu_result = "未上传"
    if args.skip_feishu_upload:
        feishu_result = "已跳过（--skip-feishu-upload）"
    elif args.feishu_app_id and args.feishu_app_secret and args.feishu_folder_token:
        try:
            feishu_url = upload_to_feishu_drive(summary_path, args.feishu_app_id, args.feishu_app_secret, args.feishu_folder_token)
            feishu_result = feishu_url or "上传成功（未返回URL）"
        except (RuntimeError, urllib.error.URLError, TimeoutError) as e:
            feishu_result = f"上传失败: {e}"
    else:
        feishu_result = "未配置飞书参数（需要 APP_ID/APP_SECRET/FOLDER_TOKEN）"

    print(summary)
    print(f"\n飞书云文档上传结果: {feishu_result}")
    print(f"输出文件目录: {output_dir.resolve()}")


if __name__ == "__main__":
    main()
