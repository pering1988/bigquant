#!/usr/bin/env python3
"""简化版周度分析脚本。

核心约束（按用户诉求）：
1) 输入文件仅包含股票代码列表；
2) 基本面/技术面/当周资金流入分析由已配置大模型完成；
3) 收益统计所需的开盘/收盘（或最新收盘）价格也由大模型检索；
4) 输出建议配比策略与等额策略收益对比，并可上传飞书。
"""

import argparse
import csv
import datetime as dt
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from pathlib import Path

DATE_RE = re.compile(r"(20\d{6})")


def latest_file(input_dir: Path) -> tuple[Path, dt.date]:
    """返回目录中日期后缀(YYYYMMDD)最新的文件和该日期。"""
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
    return sorted(dated, key=lambda x: x[0])[-1][1], sorted(dated, key=lambda x: x[0])[-1][0]


def read_stock_codes(path: Path) -> list[str]:
    """读取股票代码列表（支持txt/csv）。"""
    suffix = path.suffix.lower()
    codes: list[str] = []

    if suffix in {".txt", ".list"}:
        for line in path.read_text(encoding="utf-8").splitlines():
            code = line.strip().split(",")[0]
            if code and code.lower() not in {"code", "stock_code", "symbol"}:
                codes.append(code)
    elif suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        if not rows:
            return []
        header = [h.strip().lower() for h in rows[0]]
        idx = 0
        for cand in ["code", "stock_code", "symbol"]:
            if cand in header:
                idx = header.index(cand)
                break
        data_rows = rows[1:] if any(h in {"code", "stock_code", "symbol"} for h in header) else rows
        for r in data_rows:
            if idx < len(r):
                code = r[idx].strip()
                if code:
                    codes.append(code)
    else:
        raise ValueError("仅支持 txt/csv 股票列表文件")

    # 去重并保持顺序
    dedup = []
    seen = set()
    for c in codes:
        if c not in seen:
            seen.add(c)
            dedup.append(c)
    if not dedup:
        raise ValueError("股票列表为空")
    return dedup


def parse_simple_yaml_agent(path: Path) -> dict:
    """简化解析 agent yaml，只读取 provider/model。"""
    text = path.read_text(encoding="utf-8")
    provider = re.search(r"^provider:\s*(.+)$", text, flags=re.M)
    model = re.search(r"^model:\s*(.+)$", text, flags=re.M)
    if not provider or not model:
        raise ValueError("agent yaml 缺少 provider 或 model")
    return {
        "provider": provider.group(1).strip(),
        "model": model.group(1).strip(),
    }


def call_model(agent_cfg: dict, prompt: str) -> str:
    """调用 OpenAI 兼容聊天接口（支持 dashscope 兼容网关）。"""
    # 优先使用环境变量覆盖 endpoint/key
    base_url = os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
    api_key = os.getenv("LLM_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 LLM_API_KEY / DASHSCOPE_API_KEY")

    body = {
        "model": agent_cfg["model"],
        "messages": [
            {
                "role": "system",
                "content": "你是股票研究员。必须返回严格JSON，且不要输出JSON外文本。",
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        url=f"{base_url.rstrip('/')}/chat/completions",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        data=json.dumps(body).encode("utf-8"),
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        out = json.loads(resp.read().decode("utf-8"))
    return out["choices"][0]["message"]["content"]


def build_analysis_prompt(codes: list[str], start_date: dt.date) -> str:
    """构造让大模型完成分析与行情检索的统一提示词。"""
    today = dt.date.today()
    sell_rule = "周五收盘" if today.weekday() >= 4 else "当前最新收盘"
    template = {
        "meta": {
            "start_date": str(start_date),
            "sell_rule": sell_rule,
        },
        "stocks": [
            {
                "code": "",
                "name": "",
                "industry": "",
                "fundamental_analysis": "",
                "technical_analysis": "",
                "capital_flow_analysis": "",
                "trend_next_week": "看涨/震荡/看跌",
                "suggest_weight": 0.0,
                "monday_open": 0.0,
                "sell_price": 0.0,
            }
        ],
        "notes": "可选说明",
    }
    return (
        "请对以下股票代码完成周度分析，并自行检索需要的市场与资金数据。\n"
        "股票列表：" + ",".join(codes) + "\n"
        "周期起始日：" + str(start_date) + "\n"
        "要求：\n"
        "1) 对每只股票给出基本面、技术面、当周资金流入分析与未来一周走势判断；\n"
        "2) 给出建议买入权重 suggest_weight（所有股票之和必须=1）；\n"
        "3) 给出收益计算用价格：monday_open 与 sell_price（若未到周五取最新收盘，若到周五取周五收盘）；\n"
        "4) 返回严格 JSON，结构必须匹配此模板，不允许额外文本：\n"
        + json.dumps(template, ensure_ascii=False)
    )


def normalize_and_calc(stocks: list[dict], capital: float):
    """归一化权重并计算建议配比/等额配比收益。"""
    if not stocks:
        raise ValueError("模型返回 stocks 为空")

    # 归一化权重，避免模型小数误差
    raw_weights = [max(float(s.get("suggest_weight", 0)), 0.0) for s in stocks]
    w_sum = sum(raw_weights)
    if w_sum == 0:
        weights = [1 / len(stocks)] * len(stocks)
    else:
        weights = [w / w_sum for w in raw_weights]

    rows = []
    for i, s in enumerate(stocks):
        code = str(s.get("code", "")).strip()
        name = str(s.get("name", "")).strip()
        industry = str(s.get("industry", "")).strip()
        m_open = float(s.get("monday_open", 0) or 0)
        s_price = float(s.get("sell_price", 0) or 0)
        if m_open <= 0 or s_price <= 0:
            ret = 0.0
        else:
            ret = s_price / m_open - 1

        amount_suggest = capital * weights[i]
        amount_equal = capital / len(stocks)
        pnl_suggest = amount_suggest * ret
        pnl_equal = amount_equal * ret
        rows.append(
            {
                "code": code,
                "name": name,
                "industry": industry,
                "fundamental_analysis": s.get("fundamental_analysis", ""),
                "technical_analysis": s.get("technical_analysis", ""),
                "capital_flow_analysis": s.get("capital_flow_analysis", ""),
                "trend_next_week": s.get("trend_next_week", ""),
                "suggest_weight": round(weights[i], 6),
                "monday_open": m_open,
                "sell_price": s_price,
                "ret": ret,
                "amount_suggest": amount_suggest,
                "pnl_suggest": pnl_suggest,
                "amount_equal": amount_equal,
                "pnl_equal": pnl_equal,
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict], fields: list[str]):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def _request_json(url: str, method: str = "GET", headers: dict | None = None, data: bytes | None = None):
    req = urllib.request.Request(url=url, method=method, headers=headers or {}, data=data)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def upload_to_feishu_drive(file_path: Path, app_id: str, app_secret: str, folder_token: str) -> str:
    """上传 summary.md 到飞书云盘。"""
    token_rsp = _request_json(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"},
        data=json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8"),
    )
    if token_rsp.get("code") != 0:
        raise RuntimeError(f"获取飞书token失败: {token_rsp}")
    token = token_rsp["tenant_access_token"]

    file_bytes = file_path.read_bytes()
    boundary = f"----codex-{uuid.uuid4().hex}"
    chunks = []
    for k, v in {
        "file_name": file_path.name,
        "parent_type": "explorer",
        "parent_node": folder_token,
        "size": str(len(file_bytes)),
    }.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    chunks.append(f"--{boundary}\r\n".encode())
    chunks.append(
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\nContent-Type: text/markdown\r\n\r\n'.encode()
    )
    chunks.append(file_bytes)
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())

    rsp = _request_json(
        "https://open.feishu.cn/open-apis/drive/v1/files/upload_all",
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        data=b"".join(chunks),
    )
    if rsp.get("code") != 0:
        raise RuntimeError(f"上传飞书失败: {rsp}")
    return rsp.get("data", {}).get("url") or rsp.get("data", {}).get("file_token", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", default="/home/admin/.openclaw/workspace/stockdata/south_stocklist")
    ap.add_argument("--capital", type=float, default=150000)
    ap.add_argument("--output-dir", default="skills/openclaw-southbound-weekly/output")
    ap.add_argument("--agent-yaml", default="skills/openclaw-southbound-weekly/agents/openai.yaml")
    ap.add_argument("--skip-feishu-upload", action="store_true")
    ap.add_argument("--feishu-app-id", default=os.getenv("FEISHU_APP_ID"))
    ap.add_argument("--feishu-app-secret", default=os.getenv("FEISHU_APP_SECRET"))
    ap.add_argument("--feishu-folder-token", default=os.getenv("FEISHU_FOLDER_TOKEN"))
    args = ap.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    latest, start_date = latest_file(Path(args.input_dir))
    codes = read_stock_codes(latest)
    agent_cfg = parse_simple_yaml_agent(Path(args.agent_yaml))

    prompt = build_analysis_prompt(codes, start_date)
    raw = call_model(agent_cfg, prompt)

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"模型未返回合法JSON: {e}; 原始输出: {raw[:500]}") from e

    rows = normalize_and_calc(payload.get("stocks", []), args.capital)

    write_csv(
        output_dir / "top10_selection.csv",
        rows,
        [
            "code",
            "name",
            "industry",
            "fundamental_analysis",
            "technical_analysis",
            "capital_flow_analysis",
            "trend_next_week",
            "suggest_weight",
        ],
    )
    write_csv(
        output_dir / "allocation_and_pnl.csv",
        rows,
        [
            "code",
            "name",
            "monday_open",
            "sell_price",
            "ret",
            "amount_suggest",
            "pnl_suggest",
            "amount_equal",
            "pnl_equal",
        ],
    )

    weighted_pnl = sum(r["pnl_suggest"] for r in rows)
    equal_pnl = sum(r["pnl_equal"] for r in rows)
    excess = weighted_pnl - equal_pnl

    summary = f"""# 南向资金周度组合汇总

- 周期起始日（文件后缀日期）：{start_date}
- 输入文件：{latest}
- 运行时间：{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- 模型：{agent_cfg['provider']}/{agent_cfg['model']}

## 一、输入说明

- 输入文件仅包含股票代码列表。
- 基本面、技术指标、当周资金流入与价格数据由大模型检索并分析。

## 二、策略收益

- 策略A（建议配比）收益：{weighted_pnl:,.2f} 元
- 策略B（15万元等额）收益：{equal_pnl:,.2f} 元
- 超额收益（A-B）：{excess:,.2f} 元

## 三、输出文件

- top10_selection.csv
- allocation_and_pnl.csv
"""
    summary_path = output_dir / "summary.md"
    summary_path.write_text(summary, encoding="utf-8")

    feishu_result = "未上传"
    if args.skip_feishu_upload:
        feishu_result = "已跳过（--skip-feishu-upload）"
    elif args.feishu_app_id and args.feishu_app_secret and args.feishu_folder_token:
        try:
            feishu_result = upload_to_feishu_drive(summary_path, args.feishu_app_id, args.feishu_app_secret, args.feishu_folder_token)
        except Exception as e:
            feishu_result = f"上传失败: {e}"
    else:
        feishu_result = "缺少飞书参数"

    print(summary)
    print(f"飞书上传结果: {feishu_result}")
    print(f"输出目录: {output_dir.resolve()}")


if __name__ == "__main__":
    main()
