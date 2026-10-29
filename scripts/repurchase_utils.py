"""Repurchase data utilities for Turtle Investment Framework."""

from __future__ import annotations

import re
from typing import Optional, Tuple

import pandas as pd
import requests

from format_utils import format_header, format_number


def fetch_hk_repurchase_summary(raw_code: str, unit_label: str) -> Tuple[Optional[pd.DataFrame], str]:
    """Fetch HK repurchase records from Eastmoney datacenter and build markdown section."""
    clean_code = re.sub(r"[^\d]", "", raw_code).zfill(5)
    lines = [format_header(2, "15. 股票回购"), ""]
    try:
        url = (
            f"https://datacenter-web.eastmoney.com/api/data/v1/get?"
            f"reportName=RPT_HK_BUYBACK&columns=ALL&filter=(SECURITY_CODE%3D%22{clean_code}%22)"
            f"&pageNumber=1&pageSize=50"
        )
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        res = r.json().get("result")
        if res and res.get("data"):
            data = sorted(res["data"], key=lambda x: str(x.get("TRADE_DATE", "")), reverse=True)
            df_rep = pd.DataFrame(data)
            total_cnt = res.get("count", len(data))
            total_amt_hkd = sum(float(x.get("REPO_AMT", 0) or 0) for x in data)
            latest = data[0]
            lines.append(f"- 近期累计回购笔数: **{total_cnt} 笔**")
            lines.append(
                f"- 最新回购交易日: **{str(latest.get('TRADE_DATE', ''))[:10]}** "
                f"(回购股数: {int(latest.get('REPO_NUM', 0) or 0):,}, "
                f"回购均价: {float(latest.get('AVG_PRICE', 0) or 0):.3f} {latest.get('CURRENCY', 'HKD')})"
            )
            lines.append(f"- 近期样本已回购金额: **{format_number(total_amt_hkd)} {unit_label}**")
            lines.append("")
            lines.append("> ⚠️ 港股回购数据来源于东方财富/港交所披露；注销型回购金额需在 Phase 3 结合年报/公告核实后计入 $O$。")
            return df_rep, "\n".join(lines)
    except Exception:
        pass
    lines.append("近期无重大回购记录\n")
    return None, "\n".join(lines)
