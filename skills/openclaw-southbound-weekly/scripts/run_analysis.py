#!/usr/bin/env python3
"""OpenClaw 南向资金周度分析（简化版）

更新点：
1) 不再落地本地输出文件；
2) 仅生成文档内容，并询问是否创建飞书文档并写入；
3) 价格数据统一用 akshare 获取（港股/ A股）；
4) 大模型分析统一走“系统默认配置模型”（通过环境变量指定 endpoint/model）。
"""

import argparse
import datetime as dt
import json
import os
import re
import urllib.request
from pathlib import Path

import akshare as ak

DATE_RE = re.compile(r"(20\d{6})")


def latest_file(input_dir: Path) -> tuple[Path, dt.date]:
    dated = []
    for f in input_dir.iterdir():
        if not f.is_file():
            continue
        m = DATE_RE.search(f.stem) or DATE_RE.search(f.name)
        if not m:
            continue
        d = dt.datetime.strptime(m.group(1), "%Y%m%d").date()
        dated.append((d, f))
    if not dated:
        raise FileNotFoundError("未找到包含 YYYYMMDD 日期后缀的文件")
    d, f = sorted(dated, key=lambda x: x[0])[-1]
    return f, d


def read_codes(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    codes = []
    for line in text.splitlines():
        c = line.strip().split(",")[0]
        if c and c.lower() not in {"code", "symbol", "stock_code"}:
            codes.append(c)
    if not codes:
        raise ValueError("输入文件未读取到股票代码")
    # 去重保序
    out, seen = [], set()
    for c in codes:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def is_hk(code: str) -> bool:
    c = code.upper().replace(".HK", "")
    return c.isdigit() and len(c) <= 5 or code.upper().endswith(".HK")


def norm_hk_symbol(code: str) -> str:
    c = code.upper().replace(".HK", "")
    return c.zfill(5)


def norm_a_symbol(code: str) -> str:
    return code.upper().replace(".SZ", "").replace(".SH", "")


def get_prices_from_akshare(code: str, start_date: dt.date) -> dict:
    """从 akshare 获取周一开盘与卖出价（周五收盘或最新收盘）。"""
    start = start_date.strftime("%Y%m%d")
    end = dt.date.today().strftime("%Y%m%d")

    if is_hk(code):
        symbol = norm_hk_symbol(code)
        df = ak.stock_hk_hist(symbol=symbol, period="daily", start_date=start, end_date="22220101", adjust="qfq")
    else:
        symbol = norm_a_symbol(code)
        df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start, end_date=end, adjust="")

    if df is None or len(df) == 0:
        return {"monday_open": 0.0, "sell_price": 0.0, "ret": 0.0}

    # 兼容中英文列名
    date_col = "日期" if "日期" in df.columns else ("date" if "date" in df.columns else None)
    open_col = "开盘" if "开盘" in df.columns else ("open" if "open" in df.columns else None)
    close_col = "收盘" if "收盘" in df.columns else ("close" if "close" in df.columns else None)
    if not date_col or not open_col or not close_col:
        return {"monday_open": 0.0, "sell_price": 0.0, "ret": 0.0}

    df = df.copy()
    df[date_col] = df[date_col].astype(str)
    monday_open = float(df.iloc[0][open_col])

    # 若当前已到周五（含），用本周最后一个交易日收盘；否则用最新收盘
    sell_price = float(df.iloc[-1][close_col])
    ret = (sell_price / monday_open - 1) if monday_open > 0 else 0.0
    return {"monday_open": monday_open, "sell_price": sell_price, "ret": ret}


def call_default_llm(prompt: str) -> dict:
    """调用系统默认大模型（OpenAI兼容接口）。"""
    base_url = os.getenv("SYSTEM_LLM_BASE_URL", os.getenv("LLM_BASE_URL", ""))
    api_key = os.getenv("SYSTEM_LLM_API_KEY", os.getenv("LLM_API_KEY", ""))
    model = os.getenv("SYSTEM_LLM_MODEL", os.getenv("LLM_MODEL", "default"))
    if not base_url or not api_key:
        raise RuntimeError("缺少系统默认模型配置：SYSTEM_LLM_BASE_URL / SYSTEM_LLM_API_KEY")

    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是股票研究员，请严格返回JSON，不要输出额外文本。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        data=json.dumps(body).encode("utf-8"),
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    content = out["choices"][0]["message"]["content"]
    return json.loads(content)


def analyze_with_llm(codes: list[str], start_date: dt.date) -> list[dict]:
    template = {
        "stocks": [
            {
                "code": "00700.HK",
                "name": "",
                "industry": "",
                "fundamental_analysis": "",
                "technical_analysis": "",
                "capital_flow_analysis": "",
                "trend_next_week": "看涨/震荡/看跌",
                "suggest_weight": 0.1,
            }
        ]
    }
    prompt = (
        f"请基于股票代码列表做未来一周分析（起始日={start_date}）：{','.join(codes)}。"
        "你需要给出每只股票的基本面、技术面、资金流分析，和建议配比 suggest_weight。"
        "返回严格JSON，结构如下："
        + json.dumps(template, ensure_ascii=False)
    )
    payload = call_default_llm(prompt)
    stocks = payload.get("stocks", [])
    if not stocks:
        raise RuntimeError("大模型未返回有效 stocks")
    # 权重归一化
    ws = [max(float(x.get("suggest_weight", 0)), 0) for x in stocks]
    s = sum(ws)
    if s == 0:
        for x in stocks:
            x["suggest_weight"] = 1 / len(stocks)
    else:
        for i, x in enumerate(stocks):
            x["suggest_weight"] = ws[i] / s
    return stocks


def get_feishu_token(app_id: str, app_secret: str) -> str:
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
        data=json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8"),
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("code") != 0:
        raise RuntimeError(f"获取飞书 token 失败: {data}")
    return data["tenant_access_token"]


def create_feishu_doc_and_write(title: str, content: str, app_id: str, app_secret: str) -> str:
    """创建飞书 docx 文档并写入纯文本内容，返回文档链接。"""
    token = get_feishu_token(app_id, app_secret)

    create_req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/docx/v1/documents",
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"title": title}).encode("utf-8"),
    )
    with urllib.request.urlopen(create_req, timeout=30) as resp:
        created = json.loads(resp.read().decode("utf-8"))
    if created.get("code") != 0:
        raise RuntimeError(f"创建飞书文档失败: {created}")

    document_id = created["data"]["document"]["document_id"]

    # 追加段落到根节点（使用 document_id 作为 root_block_id）
    blocks = [ln for ln in content.splitlines() if ln.strip()]
    children = []
    for line in blocks[:200]:
        children.append({"block_type": 2, "paragraph": {"elements": [{"text_run": {"content": line}}]}})

    write_req = urllib.request.Request(
        f"https://open.feishu.cn/open-apis/docx/v1/documents/{document_id}/blocks/{document_id}/children",
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"children": children}).encode("utf-8"),
    )
    with urllib.request.urlopen(write_req, timeout=30) as resp:
        wr = json.loads(resp.read().decode("utf-8"))
    if wr.get("code") != 0:
        raise RuntimeError(f"写入飞书文档失败: {wr}")

    return f"https://{os.getenv('FEISHU_DOMAIN', 'feishu.cn')}/docx/{document_id}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", default="/home/admin/.openclaw/workspace/stockdata/south_stocklist")
    ap.add_argument("--capital", type=float, default=150000)
    ap.add_argument("--yes", action="store_true", help="自动确认创建飞书文档")
    ap.add_argument("--feishu-app-id", default=os.getenv("FEISHU_APP_ID"))
    ap.add_argument("--feishu-app-secret", default=os.getenv("FEISHU_APP_SECRET"))
    args = ap.parse_args()

    latest, start_date = latest_file(Path(args.input_dir))
    codes = read_codes(latest)
    stocks = analyze_with_llm(codes, start_date)

    # 用 akshare 获取价格并计算收益
    rows = []
    for s in stocks:
        code = str(s.get("code") or "").strip() or codes[0]
        prices = get_prices_from_akshare(code, start_date)
        row = {
            "code": code,
            "name": s.get("name", ""),
            "industry": s.get("industry", ""),
            "fundamental_analysis": s.get("fundamental_analysis", ""),
            "technical_analysis": s.get("technical_analysis", ""),
            "capital_flow_analysis": s.get("capital_flow_analysis", ""),
            "trend_next_week": s.get("trend_next_week", ""),
            "suggest_weight": float(s.get("suggest_weight", 0)),
            **prices,
        }
        row["amount_suggest"] = args.capital * row["suggest_weight"]
        row["pnl_suggest"] = row["amount_suggest"] * row["ret"]
        row["amount_equal"] = args.capital / len(stocks)
        row["pnl_equal"] = row["amount_equal"] * row["ret"]
        rows.append(row)

    weighted = sum(x["pnl_suggest"] for x in rows)
    equal = sum(x["pnl_equal"] for x in rows)
    excess = weighted - equal

    summary = f"""# 南向资金周度组合汇总

- 周期起始日：{start_date}
- 输入文件：{latest}
- 执行时间：{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- 数据来源：AKShare（港股 `stock_hk_hist` / A股 `stock_zh_a_hist`）
- 模型来源：系统默认大模型配置

## 一、未来一周分析（模型生成）
"""
    for r in rows:
        summary += (
            f"\n### {r['code']} {r['name']}\n"
            f"- 行业：{r['industry']}\n"
            f"- 基本面：{r['fundamental_analysis']}\n"
            f"- 技术面：{r['technical_analysis']}\n"
            f"- 资金流：{r['capital_flow_analysis']}\n"
            f"- 趋势判断：{r['trend_next_week']}\n"
            f"- 建议权重：{r['suggest_weight']:.2%}\n"
        )

    summary += f"""
## 二、收益对比（价格来自 AKShare）

- 策略A（建议配比）收益：{weighted:,.2f} 元
- 策略B（15万元等额）收益：{equal:,.2f} 元
- 超额收益（A-B）：{excess:,.2f} 元
"""

    print("\n文档已生成（仅内存，不写本地文件）。")
    print("\n是否创建飞书文档并写入上述内容？[y/N]")
    answer = "y" if args.yes else input().strip().lower()

    if answer in {"y", "yes"}:
        if not args.feishu_app_id or not args.feishu_app_secret:
            raise RuntimeError("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET")
        title = f"南向周度组合_{start_date}"
        url = create_feishu_doc_and_write(title, summary, args.feishu_app_id, args.feishu_app_secret)
        print(f"飞书文档已创建：{url}")
    else:
        print("已取消创建飞书文档。")


if __name__ == "__main__":
    main()
