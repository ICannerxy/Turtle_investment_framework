"""quality_control.py — 杠杆1: deterministic pre-computation of error-prone metrics.

Parses ``data_pack_market.md`` (百万元 tables) and emits ``computed_metrics.md``
with five CM sections the qualitative report MUST cite verbatim (禁止重算).
This eliminates the LLM unit-conversion / arithmetic-hallucination error class
(母公司单位错、分红率 100% vs 111.75%、Capex/D&A 均值 = 单年值 等).

CLI::

    python3 scripts/quality_control.py \
        --input  output/{code}_{name}/data_pack_market.md \
        --output output/{code}_{name}/computed_metrics.md \
        [--pe-bands 10,15,20,25,30] [--discounts 1.0,0.9,0.8,0.7]

Exit codes: 0 success · 2 input missing / unparseable.
"""

from __future__ import annotations

import argparse
import os
import re
import statistics
import sys
from typing import Dict, List, Optional

from format_utils import format_number, format_table, format_header

HEADER_WARNING = (
    "> ⚠️ 以下数值由 Python 确定性计算，报告中必须直接引用（标注 `[src: CM§N]`），"
    "**禁止重算**。百万元→亿元 = 原值 ÷ 100。\n"
)


# ─────────────────────────── pure functions ───────────────────────────

def to_yi(x_mn: Optional[float]) -> Optional[float]:
    """百万元 → 亿元 (÷100). None-tolerant."""
    if x_mn is None:
        return None
    return x_mn / 100.0


def multi_year_stats(values: List[Optional[float]]) -> Dict[str, Optional[float]]:
    """Mean / median over the non-None values. None-tolerant."""
    vals = [v for v in values if v is not None]
    if not vals:
        return {"mean": None, "median": None, "n": 0}
    return {"mean": sum(vals) / len(vals), "median": statistics.median(vals), "n": len(vals)}


def payout_ratio(div_total: Optional[float], np: Optional[float]) -> Optional[float]:
    """Dividend payout ratio (%) = 总分红 / 归母净利润 × 100. None-tolerant."""
    if div_total is None or np in (None, 0):
        return None
    return div_total / np * 100.0


def yoy_pct(curr: Optional[float], prev: Optional[float]) -> Optional[float]:
    """Year-over-year change (%). None-tolerant; prev==0 → None."""
    if curr is None or prev in (None, 0):
        return None
    return (curr - prev) / abs(prev) * 100.0


def pe_valuation(eps: Optional[float], pe: Optional[float],
                 discount: Optional[float]) -> Optional[float]:
    """Target price = EPS × PE × discount. None-tolerant."""
    if eps is None or pe is None or discount is None:
        return None
    return eps * pe * discount


# ─────────────────────────── md-table parser ──────────────────────────

def _num(cell: Optional[str]) -> Optional[float]:
    """Parse a table cell to float, stripping commas / annotations. None-tolerant."""
    if cell is None:
        return None
    s = cell.strip().replace(",", "").replace("†", "").replace("*", "")
    s = s.split("(")[0].split("（")[0].strip()
    if s in ("", "—", "-", "–", "N/A"):
        return None
    m = re.search(r"-?\d+\.?\d*", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _rows(block: str) -> List[List[str]]:
    out = []
    for line in block.splitlines():
        line = line.strip()
        if line.startswith("|") and line.endswith("|"):
            out.append([c.strip() for c in line.strip("|").split("|")])
    return out


def _is_sep(row: List[str]) -> bool:
    return bool(row) and all(set(c) <= set("-: ") for c in row)


def parse_sections(text: str) -> Dict[str, str]:
    """Split into {token: block} keyed by section number token ('3', '3P', '12')."""
    pat = re.compile(r"^##\s+(\d+[A-Za-z]?)\.\s", re.M)
    matches = list(pat.finditer(text))
    sections = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[m.group(1)] = text[m.start():end]
    return sections


def parse_matrix(block: str):
    """item×year table → (year_cols, {item: {year: float}})."""
    rows = [r for r in _rows(block) if not _is_sep(r)]
    if len(rows) < 2:
        return [], {}
    cols = rows[0][1:]
    data: Dict[str, Dict[str, Optional[float]]] = {}
    for r in rows[1:]:
        if not r or not r[0]:
            continue
        data[r[0]] = {col: _num(r[j + 1] if j + 1 < len(r) else None)
                      for j, col in enumerate(cols)}
    return cols, data


def parse_kv(block: str) -> Dict[str, str]:
    kv = {}
    for r in _rows(block):
        if not _is_sep(r) and len(r) >= 2:
            kv[r[0]] = r[1]
    return kv


def find_item(data: Dict[str, dict], *names: str) -> Optional[str]:
    for name in names:
        for k in data:
            if k.replace(" ", "").startswith(name):
                return k
    for name in names:
        for k in data:
            if name in k:
                return k
    return None


def _series(data, item_key, years):
    row = data.get(item_key, {}) if item_key else {}
    return [row.get(y) for y in years]


def _full_years(cols: List[str], limit: int = 5) -> List[str]:
    return [c for c in cols if re.fullmatch(r"\d{4}", c)][:limit]


def _yi_cells(values):
    return [format_number(to_yi(v), divider=1) for v in values]


# ─────────────────────────── CM section builders ──────────────────────

def build_report(sections: Dict[str, str], pe_bands: List[float],
                 discounts: List[float]) -> str:
    parts: List[str] = ["# 计算结果 — computed_metrics.md（Python 确定性预算）\n", HEADER_WARNING]

    inc_cols, inc = parse_matrix(sections["3"]) if "3" in sections else ([], {})
    bs_cols, bs = parse_matrix(sections["4"]) if "4" in sections else ([], {})
    cf_cols, cf = parse_matrix(sections["5"]) if "5" in sections else ([], {})
    fin_cols, fin = parse_matrix(sections["12"]) if "12" in sections else ([], {})
    kv1 = parse_kv(sections["1"]) if "1" in sections else {}

    years = _full_years(inc_cols)

    # ── CM§1 亿元对照表 ──
    parts.append(format_header(2, "CM§1 亿元对照表（百万元 ÷ 100 = 亿元）"))
    if not years:
        parts.append("> ⚠️ CM§1 跳过：缺少 §3 利润表年度数据。\n")
    else:
        y0 = years[0]
        items = [
            ("营业收入", inc, ("营业收入", "营业额")),
            ("营业利润", inc, ("营业利润", "经营溢利")),
            ("净利润", inc, ("净利润", "除税后溢利")),
            ("归母净利润", inc, ("归母净利润", "股东应占溢利")),
            ("总资产", bs, ("总资产",)),
            ("归母所有者权益", bs, ("归母所有者权益", "归母权益", "股东权益")),
            ("经营活动现金流(OCF)", cf, ("经营活动现金流", "OCF")),
            ("自由现金流(FCF)", cf, ("自由现金流", "FCF")),
        ]
        headers = ["项目", f"{y0} 百万元", f"{y0} 亿元"] + [f"{y} 亿元" for y in years[1:]]
        rows = []
        for label, src, names in items:
            key = find_item(src, *names)
            vals = _series(src, key, years)
            rows.append([label, format_number(vals[0], divider=1)] + _yi_cells(vals))
        parts.append(format_table(headers, rows, ["l"] + ["r"] * (len(headers) - 1)))
        parts.append("")

    # ── CM§2 同比 ──
    parts.append(format_header(2, "CM§2 同比变化率（%）"))
    if len(years) < 2:
        parts.append("> ⚠️ CM§2 跳过：可比年度不足 2 年。\n")
    else:
        yoy_items = [("营业收入", inc, ("营业收入", "营业额")), ("归母净利润", inc, ("归母净利润", "股东应占溢利")),
                     ("净利润", inc, ("净利润", "除税后溢利")), ("营业利润", inc, ("营业利润", "经营溢利"))]
        headers = ["指标"] + [f"{years[i]} vs {years[i + 1]}" for i in range(len(years) - 1)]
        rows = []
        for label, src, names in yoy_items:
            vals = _series(src, find_item(src, *names), years)
            cells = [format_number(yoy_pct(vals[i], vals[i + 1]), divider=1) for i in range(len(years) - 1)]
            rows.append([label] + cells)
        parts.append(format_table(headers, rows, ["l"] + ["r"] * (len(years) - 1)))
        parts.append("")

    # ── CM§3 多年统计 ──
    parts.append(format_header(2, "CM§3 多年统计（均值 / 中位数）"))
    if not years:
        parts.append("> ⚠️ CM§3 跳过：缺少年度序列。\n")
    else:
        stat_specs = [
            ("营业收入(亿元)", [to_yi(v) for v in _series(inc, find_item(inc, "营业收入", "营业额"), years)]),
            ("归母净利润(亿元)", [to_yi(v) for v in _series(inc, find_item(inc, "归母净利润", "股东应占溢利"), years)]),
            ("ROE(%)", _series(fin, find_item(fin, "ROE", "净资产收益率"), _full_years(fin_cols))),
            ("毛利率(%)", _series(fin, find_item(fin, "毛利率"), _full_years(fin_cols))),
            ("净利率(%)", _series(fin, find_item(fin, "净利率"), _full_years(fin_cols))),
        ]
        rows = []
        for label, vals in stat_specs:
            st = multi_year_stats(vals)
            rows.append([label, format_number(st["mean"], divider=1),
                         format_number(st["median"], divider=1), str(st["n"])])
        parts.append(format_table(["指标", "均值", "中位数", "年数"], rows, ["l", "r", "r", "r"]))
        parts.append("")

    # ── CM§4 分红支付率 ──
    parts.append(format_header(2, "CM§4 分红支付率（总分红 ÷ 归母净利润）"))
    div_by_year = _dividends_by_year(sections.get("6", ""))
    np_key = find_item(inc, "归母净利润", "股东应占溢利")
    if not div_by_year or not np_key:
        parts.append("> ⚠️ CM§4 跳过：缺少 §6 分红或 §3 归母净利润。\n")
    else:
        rows = []
        for yr in sorted(div_by_year, reverse=True):
            div = div_by_year[yr]
            npv = inc.get(np_key, {}).get(yr)
            rows.append([yr, format_number(to_yi(div), divider=1),
                         format_number(to_yi(npv), divider=1),
                         format_number(payout_ratio(div, npv), divider=1)])
        parts.append(format_table(["年度", "总分红(亿元)", "归母净利润(亿元)", "支付率(%)"],
                                  rows, ["l", "r", "r", "r"]))
        parts.append("")

    # ── CM§5 PE 估值链网格 ──
    parts.append(format_header(2, "CM§5 PE 估值链网格（目标价 = EPS × PE × 折扣）"))
    eps_key = find_item(inc, "基本EPS", "基本每股收益", "EPS", "每股基本盈利")
    eps = inc.get(eps_key, {}).get(years[0]) if (eps_key and years) else None
    price = _num(kv1.get("当前价格 (HKD)")) or _num(kv1.get("当前价格"))
    mktcap_wan = _num(kv1.get("总市值 (万港元)")) or _num(kv1.get("总市值 (万元)")) or _num(kv1.get("总市值(万元)"))
    if eps is None:
        parts.append("> ⚠️ CM§5 跳过：缺少 §3 基本EPS。\n")
    else:
        note = f"> EPS = {years[0]} 基本EPS = {format_number(eps, divider=1)} 元"
        if price is not None:
            note += f"；当前股价 = {format_number(price, divider=1)} 元"
        if mktcap_wan is not None:
            note += f"；总市值 = {format_number(mktcap_wan / 10000.0, divider=1)} 亿元（§1 万元 ÷ 10000）"
        parts.append(note + "\n")
        headers = ["PE \\ 折扣"] + [f"×{d:g}" for d in discounts]
        rows = []
        for pe in pe_bands:
            cells = [format_number(pe_valuation(eps, pe, d), divider=1) for d in discounts]
            rows.append([f"PE {pe:g}"] + cells)
        parts.append(format_table(headers, rows, ["l"] + ["r"] * len(discounts)))
        parts.append("")

    # ── CM§6 最新边际季度/中期异动监控 ──
    interim_cols = [c for c in inc_cols if not re.fullmatch(r"\d{4}", c)]
    if interim_cols:
        parts.append(format_header(2, "CM§6 最新边际季度/中期异动监控"))
        rev_key = find_item(inc, "营业收入", "营业额")
        np_key = find_item(inc, "归母净利润", "股东应占溢利")
        headers = ["期间", "累计营业收入(亿元)", "累计归母净利润(亿元)", "推算单季营收(亿元)", "推算单季归母净利(亿元)", "边际提示"]
        rows = []
        for i, col in enumerate(interim_cols):
            rev_val = to_yi(inc.get(rev_key, {}).get(col)) if rev_key else None
            np_val = to_yi(inc.get(np_key, {}).get(col)) if np_key else None
            single_rev = None
            single_np = None
            note = "累计披露"
            if re.search(r"Q1", col, re.IGNORECASE):
                single_rev, single_np, note = rev_val, np_val, "Q1单季"
            elif i + 1 < len(interim_cols):
                next_col = interim_cols[i + 1]
                yc, yn = re.match(r"\d{4}", col), re.match(r"\d{4}", next_col)
                if yc and yn and yc.group(0) == yn.group(0):
                    n_rev = to_yi(inc.get(rev_key, {}).get(next_col)) if rev_key else None
                    n_np = to_yi(inc.get(np_key, {}).get(next_col)) if np_key else None
                    is_valid_pair = False
                    if "Q3" in col.upper() and "H1" in next_col.upper():
                        note, is_valid_pair = "推算Q3单季 (9M - H1)", True
                    elif "H1" in col.upper() and "Q1" in next_col.upper():
                        note, is_valid_pair = "推算Q2单季 (H1 - Q1)", True

                    if is_valid_pair and rev_val is not None and n_rev is not None:
                        single_rev = rev_val - n_rev
                    if is_valid_pair and np_val is not None and n_np is not None:
                        single_np = np_val - n_np
            rows.append([col, format_number(rev_val, divider=1), format_number(np_val, divider=1),
                         format_number(single_rev, divider=1), format_number(single_np, divider=1), note])
        parts.append(format_table(headers, rows, ["l", "r", "r", "r", "r", "l"]))
        parts.append("\n> ⚠️ **边际异动提示**：若最新单季利润或营收显著降速/承压，D3 章节必须深入分析边际成因（如销量下滑、批价松动、成本刚性、去库存等），严禁只看全财年而遗漏最新周期拐点。\n")

    # ── CM§7 股票回购与股东回报核验提示 ──
    rep_info = _parse_repurchase_info(sections.get("15", ""))
    if rep_info:
        parts.append(format_header(2, "CM§7 股票回购与股东回报核验提示"))
        parts.append(
            f"> ⚠️ **回购数据监测**：§15 监测到近期累计回购 **{rep_info['count']}** 笔，已回购金额约 **{rep_info['amount']} {rep_info['unit']}**（最新交易日：{rep_info['latest_date']}）。\n"
            "> - **附注核实铁律**：LLM 必须查阅 `data_pack_report.md` 中的 `## RET` 章节或财报中报，核验回购股份是否**注销**以及是否有董事会批准的《未来三年股东回报规划》。\n"
            "> - **定量联动**：若确认为注销型回购或有确定性分红/回购承诺，必须按年化金额计入定量模型中的 $O$（年均注销型回购），并在 §17.11 穿透回报率网格中叠加加法修正项，**严禁盲目默认 $O=0$**。\n"
        )

    parts.append("---\n*computed_metrics.md · 由 scripts/quality_control.py 生成 · 内部工件（非交付物）*")
    return "\n".join(parts) + "\n"


def _parse_repurchase_info(block: str) -> Optional[Dict[str, str]]:
    """Parse repurchase summary from §15 block."""
    if not block:
        return None
    m_cnt = re.search(r"近期累计回购笔数:\s*\*\*(\d+)\s*笔\*\*", block)
    m_amt = re.search(r"近期样本已回购金额:\s*\*\*([0-9.,]+)\s*([^\*]+)\*\*", block)
    m_date = re.search(r"最新回购交易日:\s*\*\*([^\*]+)\*\*", block)
    if m_amt or m_cnt:
        return {
            "count": m_cnt.group(1) if m_cnt else "若干",
            "amount": m_amt.group(1) if m_amt else "—",
            "unit": m_amt.group(2).strip() if m_amt else "",
            "latest_date": m_date.group(1) if m_date else "—",
        }
    return None


def _dividends_by_year(block: str) -> Dict[str, float]:
    """§6 分红历史 → {年度: 总分红(百万元) 累加}."""
    if not block:
        return {}
    rows = [r for r in _rows(block) if not _is_sep(r)]
    if len(rows) < 2:
        return {}
    header = rows[0]
    # locate 年度 col (0) and 总分红 col (contains 总分红)
    div_idx = next((i for i, h in enumerate(header) if "总分红" in h), len(header) - 1)
    agg: Dict[str, float] = {}
    for r in rows[1:]:
        yr = r[0].strip()
        if not re.fullmatch(r"\d{4}", yr):
            continue
        val = _num(r[div_idx] if div_idx < len(r) else None)
        if val is not None:
            agg[yr] = agg.get(yr, 0.0) + val
    return agg


# ─────────────────────────────── CLI ──────────────────────────────────

def _parse_floats(s: str) -> List[float]:
    return [float(x) for x in s.split(",") if x.strip()]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="预算定性报告易错指标 → computed_metrics.md")
    ap.add_argument("--input", required=True, help="data_pack_market.md 路径")
    ap.add_argument("--output", required=True, help="computed_metrics.md 输出路径")
    ap.add_argument("--pe-bands", default="10,15,20,25,30", help="PE 档位, 逗号分隔")
    ap.add_argument("--discounts", default="1.0,0.9,0.8,0.7", help="折扣系数, 逗号分隔")
    args = ap.parse_args(argv)

    if not os.path.isfile(args.input):
        sys.stderr.write(f"[quality_control] 输入文件不存在: {args.input}\n")
        return 2
    with open(args.input, "r", encoding="utf-8") as f:
        text = f.read()
    sections = parse_sections(text)
    if not sections:
        sys.stderr.write("[quality_control] 无法解析任何数据板块（§N）。\n")
        return 2

    report = build_report(sections, _parse_floats(args.pe_bands), _parse_floats(args.discounts))
    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(report)
    sys.stderr.write(f"[quality_control] 已写入 {args.output}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
