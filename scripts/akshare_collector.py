#!/usr/bin/env python3
"""Turtle Investment Framework - AkShare Data Collector (Phase 1A Fallback).

Free alternative to tushare_collector.py using AkShare and yfinance (no API token required).
Supports:
    - A-Shares: 600887, 000858, 600519
    - HK-Shares: 06049.HK (保利物业), 00700.HK (腾讯控股), 09988.HK (阿里巴巴)

Usage:
    python3 scripts/akshare_collector.py --code 600887
    python3 scripts/akshare_collector.py --code 06049.HK
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests

try:
    import akshare as ak
except ImportError:
    print("ERROR: akshare is not installed. Please run: pip install akshare", file=sys.stderr)
    sys.exit(1)

try:
    import yfinance as yf
    _yf_available = True
except ImportError:
    _yf_available = False

from format_utils import format_number, format_table, format_header
from repurchase_utils import fetch_hk_repurchase_summary
from tushare_modules import (
    InfrastructureMixin,
    DerivedMetricsMixin,
    AssemblyMixin,
    WarningsCollector,
    HK_INCOME_MAP,
    HK_BALANCE_MAP,
    HK_CASHFLOW_MAP,
)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def normalize_code(code: str) -> Tuple[str, str, str]:
    """Normalize input code into (ts_code, raw_code, market_prefix).
    
    Examples:
        600887 -> ('600887.SH', '600887', 'SH')
        000858 -> ('000858.SZ', '000858', 'SZ')
        06049 -> ('06049.HK', '06049', 'HK')
        6049 -> ('06049.HK', '06049', 'HK')
        06049.HK -> ('06049.HK', '06049', 'HK')
    """
    clean = code.strip().upper()
    if clean.endswith(".HK"):
        raw = clean[:-3].zfill(5)
        return f"{raw}.HK", raw, "HK"
    if clean.endswith(".US"):
        raw = clean[:-3]
        return f"{raw}.US", raw, "US"
    if clean.startswith("HK") and clean[2:].isdigit():
        raw = clean[2:].zfill(5)
        return f"{raw}.HK", raw, "HK"
    if clean.isdigit() and len(clean) <= 5:
        raw = clean.zfill(5)
        return f"{raw}.HK", raw, "HK"
    if "." in clean:
        raw, mkt = clean.split(".", 1)
        return f"{raw}.{mkt}", raw, mkt
    if clean.startswith(("60", "68", "90")):
        return f"{clean}.SH", clean, "SH"
    elif clean.startswith(("00", "30", "20")):
        return f"{clean}.SZ", clean, "SZ"
    elif clean.startswith(("8", "4", "92")):
        return f"{clean}.BJ", clean, "BJ"
    return f"{clean}.SH", clean, "SH"


class AkshareClient(InfrastructureMixin, DerivedMetricsMixin, AssemblyMixin):
    """Data collector leveraging AkShare, Sina Finance, and yfinance."""

    def __init__(self, cache_dir: Optional[str] = None):
        self._store: Dict[str, pd.DataFrame] = {}
        self._cache_dir = cache_dir or os.path.join(PROJECT_ROOT, "output", ".akshare_cache")
        self.warnings = WarningsCollector()
        self._fy_end_month: int = 12
        self._currency: str = "CNY"
        self._company_name: str = ""
        self._total_shares: float = 0.0
        self._float_shares: float = 0.0
        self._yf_available = _yf_available
        os.makedirs(self._cache_dir, exist_ok=True)

    def _is_hk(self, ts_code: str) -> bool:
        return ts_code.endswith(".HK")

    def _is_us(self, ts_code: str) -> bool:
        return ts_code.endswith(".US")

    def _cache_path(self, key: str) -> str:
        return os.path.join(self._cache_dir, f"{key}.parquet")

    def _read_cache(self, key: str, max_age_days: int = 7) -> Optional[pd.DataFrame]:
        path = self._cache_path(key)
        if os.path.exists(path):
            mtime = os.path.getmtime(path)
            if (time.time() - mtime) < max_age_days * 86400:
                try:
                    return pd.read_parquet(path)
                except Exception:
                    pass
        return None

    def _write_cache(self, key: str, df: pd.DataFrame) -> None:
        if df is not None and not df.empty:
            try:
                df.to_parquet(self._cache_path(key), index=False)
            except Exception:
                pass

    # --- Section 1: Basic info ---

    def get_basic_info(self, ts_code: str) -> str:
        ts_code, raw_code, mkt = normalize_code(ts_code)
        if self._is_hk(ts_code):
            return self._get_basic_info_hk(ts_code, raw_code)

        # 1. Sina real-time quote for price & name
        name = raw_code
        close = None
        trade_date = pd.Timestamp.now().strftime("%Y%m%d")
        try:
            url = f"http://hq.sinajs.cn/list={mkt.lower()}{raw_code}"
            r = requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=5)
            if '"' in r.text:
                parts = r.text.split('"')[1].split(",")
                if len(parts) > 30:
                    name = parts[0]
                    close = float(parts[3])
                    trade_date = parts[30].replace("-", "")
        except Exception:
            pass
        self._company_name = name

        # 2. Get share count from top 10 shareholders
        total_share = None
        float_share = None
        industry = "—"

        try:
            df_gdr = ak.stock_gdfx_top_10_em(symbol=f"{mkt.lower()}{raw_code}")
            if not df_gdr.empty:
                row0 = df_gdr.iloc[0]
                holds = float(row0.get("持股数", 0))
                ratio = float(row0.get("占总股本持股比例", 0))
                if holds > 0 and ratio > 0:
                    total_share = holds / (ratio / 100.0)
                    float_share = total_share
                    self._total_shares = total_share
                    self._float_shares = float_share
        except Exception:
            pass

        # 3. Valuation & indicators via stock_financial_abstract
        pe_ttm = None
        pb = None

        try:
            df_abs = self._read_cache(f"abs_{raw_code}")
            if df_abs is None:
                df_abs = ak.stock_financial_abstract(symbol=raw_code)
                self._write_cache(f"abs_{raw_code}", df_abs)
            
            if close:
                eps_row = df_abs[df_abs["指标"] == "基本每股收益"]
                bps_row = df_abs[df_abs["指标"] == "每股净资产"]
                if not eps_row.empty:
                    val_cols = [c for c in eps_row.columns if c.isdigit()]
                    if val_cols:
                        latest_eps = float(eps_row[val_cols[0]].values[0])
                        if latest_eps > 0:
                            pe_ttm = round(close / latest_eps, 2)
                if not bps_row.empty:
                    val_cols = [c for c in bps_row.columns if c.isdigit()]
                    if val_cols:
                        latest_bps = float(bps_row[val_cols[0]].values[0])
                        if latest_bps > 0:
                            pb = round(close / latest_bps, 2)
        except Exception:
            pass

        total_mv_wan = (close * total_share / 10000.0) if (close and total_share) else None
        circ_mv_wan = (close * float_share / 10000.0) if (close and float_share) else None
        total_market_cap = (total_mv_wan / 100.0) if total_mv_wan else None

        daily_record = {
            "ts_code": ts_code,
            "trade_date": trade_date,
            "close": close,
            "pe_ttm": pe_ttm,
            "pb": pb,
            "total_mv": total_mv_wan,
            "circ_mv": circ_mv_wan,
            "total_market_cap": total_market_cap,
            "total_share": (total_share / 10000.0) if total_share else None,
            "float_share": (float_share / 10000.0) if float_share else None,
        }
        self._store["basic_info"] = pd.DataFrame([daily_record])

        val_rows = [
            ["当前价格", f"{close:.2f}" if close else "—"],
            ["PE (TTM)", f"{pe_ttm:.2f}" if pe_ttm else "—"],
            ["PB", f"{pb:.2f}" if pb else "—"],
            ["总市值 (万元)", format_number(total_mv_wan, divider=1, decimals=2)],
            ["流通市值 (万元)", format_number(circ_mv_wan, divider=1, decimals=2)],
        ]

        lines = [format_header(2, "1. 基本信息"), ""]
        info_table = format_table(
            ["项目", "内容"],
            [
                ["股票代码", ts_code],
                ["公司名称", name],
                ["全称", name],
                ["行业", industry],
                ["地区", "中国"],
                ["交易所", mkt],
                ["上市日期", "—"],
            ] + val_rows,
            alignments=["l", "r"],
        )
        lines.append(info_table)
        return "\n".join(lines)

    def _get_basic_info_hk(self, ts_code: str, raw_code: str) -> str:
        self._currency = "HKD"
        name = raw_code
        close = None
        pe_ttm = None

        # 1. Sina HK quote
        try:
            url = f"http://hq.sinajs.cn/list=rt_hk{raw_code}"
            r = requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=5)
            if '"' in r.text:
                parts = r.text.split('"')[1].split(",")
                if len(parts) > 15:
                    name = parts[1] or parts[0]
                    close = float(parts[6]) if float(parts[6]) > 0 else float(parts[3])
                    if len(parts) > 13 and float(parts[13]) > 0:
                        pe_ttm = float(parts[13])
        except Exception:
            pass

        self._company_name = name

        # 2. Total shares & PB via yfinance or financial analysis indicator
        total_share = None
        pb = None
        market_cap_wan = None

        if self._yf_available:
            try:
                yf_sym = f"{raw_code.lstrip('0').zfill(4)}.HK"
                t = yf.Ticker(yf_sym)
                info = t.info
                if not close and info.get("regularMarketPrice"):
                    close = float(info.get("regularMarketPrice"))
                if not pe_ttm and info.get("trailingPE"):
                    pe_ttm = float(info.get("trailingPE"))
                if info.get("priceToBook"):
                    pb = float(info.get("priceToBook"))
                if info.get("sharesOutstanding"):
                    total_share = float(info.get("sharesOutstanding"))
                if info.get("marketCap"):
                    market_cap_wan = float(info.get("marketCap")) / 10000.0
            except Exception:
                pass

        if not total_share or not pb:
            try:
                df_ind = ak.stock_financial_hk_analysis_indicator_em(symbol=raw_code)
                if not df_ind.empty:
                    row0 = df_ind.iloc[0]
                    h_profit = float(row0.get("HOLDER_PROFIT", 0) or 0)
                    eps = float(row0.get("BASIC_EPS", 0) or 0)
                    bps = float(row0.get("BPS", 0) or 0)
                    if not total_share and h_profit > 0 and eps > 0:
                        total_share = h_profit / eps
                    if not pb and close and bps > 0:
                        pb = round(close / bps, 2)
            except Exception:
                pass

        if not market_cap_wan and close and total_share:
            market_cap_wan = (close * total_share) / 10000.0

        self._total_shares = total_share or 0.0
        self._float_shares = total_share or 0.0

        daily_record = {
            "ts_code": ts_code,
            "trade_date": pd.Timestamp.now().strftime("%Y%m%d"),
            "close": close,
            "pe_ttm": pe_ttm,
            "pb": pb,
            "total_mv": market_cap_wan,
            "total_market_cap": (market_cap_wan / 100.0) if market_cap_wan else None,
            "circ_mv": market_cap_wan,
            "total_share": (total_share / 10000.0) if total_share else None,
            "float_share": (total_share / 10000.0) if total_share else None,
        }
        self._store["basic_info"] = pd.DataFrame([daily_record])

        val_rows = [
            ["当前价格 (HKD)", f"{close:.2f}" if close else "—"],
            ["PE (TTM)", f"{pe_ttm:.2f}" if pe_ttm else "—"],
            ["PB", f"{pb:.2f}" if pb else "—"],
            ["总市值 (万港元)", format_number(market_cap_wan, divider=1, decimals=2)],
        ]

        lines = [format_header(2, "1. 基本信息"), ""]
        info_table = format_table(
            ["项目", "内容"],
            [
                ["股票代码", ts_code],
                ["公司名称", name],
                ["交易所", "HKEX"],
                ["市场", "港股主板"],
            ] + val_rows,
            alignments=["l", "r"],
        )
        lines.append(info_table)
        return "\n".join(lines)

    # --- Section 2: Market data ---

    def get_market_data(self, ts_code: str) -> str:
        ts_code, raw_code, mkt = normalize_code(ts_code)
        if self._is_hk(ts_code):
            return self._get_market_data_hk(ts_code, raw_code)

        lines = [format_header(2, "2. 市场行情"), ""]
        cache_key = f"daily_1y_{raw_code}"
        df = self._read_cache(cache_key, max_age_days=1)
        if df is None:
            start_date = (pd.Timestamp.now() - pd.DateOffset(years=1)).strftime("%Y%m%d")
            end_date = pd.Timestamp.now().strftime("%Y%m%d")
            try:
                df = ak.stock_zh_a_daily(symbol=f"{mkt.lower()}{raw_code}", start_date=start_date, end_date=end_date)
                self._write_cache(cache_key, df)
            except Exception:
                df = pd.DataFrame()

        if df is not None and not df.empty:
            df["trade_date"] = pd.to_datetime(df["date"]).dt.strftime("%Y%m%d")
            df = df.sort_values("trade_date", ascending=False).reset_index(drop=True)
            latest_close = df.iloc[0]["close"]
            high_52w = df["high"].max()
            low_52w = df["low"].min()
            high_date = df.loc[df["high"].idxmax(), "trade_date"]
            low_date = df.loc[df["low"].idxmin(), "trade_date"]
            avg_vol = df["volume"].mean() if "volume" in df.columns else (df["amount"].mean() / latest_close if "amount" in df.columns else 0)

            lines.append("*来源: Sina Finance (AkShare 降级)*\n")
            table = format_table(
                ["指标", "数值"],
                [
                    ["最新收盘价", f"{latest_close:.2f}"],
                    ["52周最高", f"{high_52w:.2f} ({high_date})"],
                    ["52周最低", f"{low_52w:.2f} ({low_date})"],
                    ["52周涨跌幅", f"{(latest_close / low_52w - 1) * 100:.1f}% (自低点)"],
                    ["日均成交量 (股)", f"{avg_vol:,.0f}"],
                ],
                alignments=["l", "r"],
            )
            lines.append(table)
            return "\n".join(lines)

        lines.append("数据缺失\n")
        return "\n".join(lines)

    def _get_market_data_hk(self, ts_code: str, raw_code: str) -> str:
        lines = [format_header(2, "2. 市场行情"), ""]
        if self._yf_available:
            try:
                yf_sym = f"{raw_code.lstrip('0').zfill(4)}.HK"
                t = yf.Ticker(yf_sym)
                info = t.info
                close = info.get("regularMarketPrice") or info.get("previousClose")
                high_52w = info.get("fiftyTwoWeekHigh")
                low_52w = info.get("fiftyTwoWeekLow")
                mkt_cap = info.get("marketCap")
                avg_vol = info.get("averageDailyVolume10Day")

                rows = [["最新收盘价 (HKD)", f"{close:.2f}" if close else "—"]]
                if high_52w:
                    rows.append(["52周最高", f"{high_52w:.2f}"])
                if low_52w:
                    rows.append(["52周最低", f"{low_52w:.2f}"])
                if close and low_52w and low_52w > 0:
                    rows.append(["52周涨跌幅", f"{(close / low_52w - 1) * 100:.1f}% (自低点)"])
                if avg_vol:
                    rows.append(["日均成交量 (股)", f"{avg_vol:,.0f}"])
                if mkt_cap:
                    rows.append(["总市值 (百万港元)", format_number(mkt_cap, divider=1e6)])

                table = format_table(["指标", "数值"], rows, alignments=["l", "r"])
                lines.append(table)
                lines.append("\n*来源: yfinance*")
                return "\n".join(lines)
            except Exception:
                pass
        lines.append("港股行情数据获取中\n")
        return "\n".join(lines)

    # --- Section 3: Income Statement ---

    def get_income(self, ts_code: str, report_type: str = "1") -> str:
        ts_code, raw_code, mkt = normalize_code(ts_code)
        if self._is_hk(ts_code):
            return self._get_income_hk(ts_code, raw_code)

        symbol_em = f"{mkt}{raw_code}"
        cache_key = f"ps_{symbol_em}"
        raw_df = self._read_cache(cache_key, max_age_days=7)
        if raw_df is None:
            try:
                raw_df = ak.stock_profit_sheet_by_report_em(symbol=symbol_em)
                self._write_cache(cache_key, raw_df)
            except Exception as e:
                print(f"  Warning: failed to fetch profit sheet: {e}", file=sys.stderr)
                raw_df = pd.DataFrame()

        section_label = "3P. 母公司利润表" if report_type == "6" else "3. 合并利润表"
        lines = [format_header(2, section_label), ""]
        if raw_df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        df = pd.DataFrame()
        df["ts_code"] = ts_code
        df["end_date"] = raw_df["REPORT_DATE"].astype(str).str[:10].str.replace("-", "")
        df["report_type"] = report_type
        
        mapping = {
            "revenue": "TOTAL_OPERATE_INCOME",
            "oper_cost": "OPERATE_COST",
            "biz_tax_surch": "OPERATE_TAX_ADD",
            "sell_exp": "SALE_EXPENSE",
            "admin_exp": "MANAGE_EXPENSE",
            "rd_exp": "RESEARCH_EXPENSE",
            "finance_exp": "FINANCE_EXPENSE",
            "assets_impair_loss": "ASSET_IMPAIRMENT_LOSS",
            "credit_impair_loss": "CREDIT_IMPAIRMENT_LOSS",
            "fv_value_chg_gain": "FAIRVALUE_CHANGE_INCOME",
            "invest_income": "INVEST_INCOME",
            "asset_disp_income": "ASSET_DISPOSAL_INCOME",
            "operate_profit": "OPERATE_PROFIT",
            "non_oper_income": "NONBUSINESS_INCOME",
            "non_oper_exp": "NONBUSINESS_EXPENSE",
            "total_profit": "TOTAL_PROFIT",
            "income_tax": "INCOME_TAX",
            "n_income": "NETPROFIT",
            "n_income_attr_p": "PARENT_NETPROFIT",
            "minority_gain": "MINORITY_INTEREST",
            "basic_eps": "BASIC_EPS",
            "diluted_eps": "DILUTED_EPS",
            "dt_eps": "DEDUCT_PARENT_NETPROFIT",
        }
        for ts_col, em_col in mapping.items():
            if em_col in raw_df.columns:
                df[ts_col] = pd.to_numeric(raw_df[em_col], errors="coerce")
            else:
                df[ts_col] = 0.0

        df, years = self._prepare_display_periods(df)
        store_key = "income_parent" if report_type == "6" else "income"
        self._store[store_key] = df
        self._store[store_key + "_years"] = years

        if not years:
            lines.append("无年报数据\n")
            return "\n".join(lines)

        fields = [
            ("营业收入", "revenue"),
            ("营业成本", "oper_cost"),
            ("税金及附加", "biz_tax_surch"),
            ("销售费用", "sell_exp"),
            ("管理费用", "admin_exp"),
            ("研发费用", "rd_exp"),
            ("财务费用", "finance_exp"),
            ("资产减值损失", "assets_impair_loss"),
            ("信用减值损失", "credit_impair_loss"),
            ("公允价值变动收益", "fv_value_chg_gain"),
            ("投资收益", "invest_income"),
            ("资产处置收益", "asset_disp_income"),
            ("营业利润", "operate_profit"),
            ("营业外收入", "non_oper_income"),
            ("营业外支出", "non_oper_exp"),
            ("利润总额", "total_profit"),
            ("所得税费用", "income_tax"),
            ("净利润", "n_income"),
            ("归母净利润", "n_income_attr_p"),
            ("少数股东损益", "minority_gain"),
            ("基本EPS", "basic_eps"),
            ("稀释EPS", "diluted_eps"),
        ]
        if report_type == "6":
            _exclude = {"minority_gain", "basic_eps", "diluted_eps", "credit_impair_loss"}
            fields = [(lbl, c) for lbl, c in fields if c not in _exclude]

        headers = ["项目 (百万元)"] + years
        rows = []
        for label, col in fields:
            row = [label]
            for _, r in df.iterrows():
                val = r.get(col)
                if col in ("basic_eps", "diluted_eps"):
                    row.append(f"{val:.2f}" if val is not None and not pd.isna(val) else "—")
                else:
                    row.append(format_number(val))
            rows.append(row)

        table = format_table(headers, rows, alignments=["l"] + ["r"] * len(years))
        lines.append(table)
        lines.append("\n*单位: 百万元 (原始数据 / 1,000,000), EPS为元/股*")
        return "\n".join(lines)

    def _get_income_hk(self, ts_code: str, raw_code: str) -> str:
        lines = [format_header(2, "3. 合并利润表"), ""]
        cache_key = f"hk_ps_all_{raw_code}"
        raw_df = self._read_cache(cache_key)
        if raw_df is None:
            try:
                raw_df = ak.stock_financial_hk_report_em(stock=raw_code, symbol="利润表", indicator="报告期")
                self._write_cache(cache_key, raw_df)
            except Exception as e:
                print(f"  Warning: failed to fetch HK profit sheet: {e}", file=sys.stderr)
                raw_df = pd.DataFrame()

        if raw_df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        rev_map = {v: k for k, v in HK_INCOME_MAP.items()}
        records = []
        for date, grp in raw_df.groupby("REPORT_DATE"):
            end_date = str(date)[:10].replace("-", "")
            rec = {"ts_code": ts_code, "end_date": end_date, "report_type": "1"}
            for _, r in grp.iterrows():
                item = r["STD_ITEM_NAME"]
                if item in rev_map:
                    col = rev_map[item]
                    rec[col] = float(r["AMOUNT"]) if r["AMOUNT"] is not None and not pd.isna(r["AMOUNT"]) else 0.0
            records.append(rec)

        pivoted = pd.DataFrame(records).sort_values("end_date", ascending=False)
        pivoted, years = self._prepare_display_periods(pivoted)
        self._store["income"] = pivoted
        self._store["income_years"] = years

        fields = [
            ("营业额", "revenue"),
            ("营运支出", "oper_cost"),
            ("销售及分销费用", "sell_exp"),
            ("行政开支", "admin_exp"),
            ("经营溢利", "operate_profit"),
            ("应占联营公司溢利", "invest_income"),
            ("融资成本", "finance_exp"),
            ("除税前溢利", "total_profit"),
            ("税项", "income_tax"),
            ("除税后溢利", "n_income"),
            ("股东应占溢利", "n_income_attr_p"),
            ("少数股东损益", "minority_gain"),
            ("每股基本盈利 (HKD)", "basic_eps"),
            ("每股摊薄盈利 (HKD)", "diluted_eps"),
        ]

        headers = ["项目 (百万港元)"] + years
        rows = []
        for label, col in fields:
            row = [label]
            for _, r in pivoted.iterrows():
                val = r.get(col)
                if col in ("basic_eps", "diluted_eps"):
                    row.append(f"{val:.2f}" if val is not None and not pd.isna(val) else "—")
                else:
                    row.append(format_number(val))
            rows.append(row)

        table = format_table(headers, rows, alignments=["l"] + ["r"] * len(years))
        lines.append(table)
        lines.append("\n*单位: 百万港元 (原始数据 / 1,000,000), EPS为港元/股*")
        return "\n".join(lines)

    def get_income_parent(self, ts_code: str) -> str:
        return self.get_income(ts_code, report_type="6")

    # --- Section 4: Balance Sheet ---

    def get_balance_sheet(self, ts_code: str, report_type: str = "1") -> str:
        ts_code, raw_code, mkt = normalize_code(ts_code)
        if self._is_hk(ts_code):
            return self._get_balance_sheet_hk(ts_code, raw_code)

        symbol_em = f"{mkt}{raw_code}"
        cache_key = f"bs_{symbol_em}"
        raw_df = self._read_cache(cache_key, max_age_days=7)
        if raw_df is None:
            try:
                raw_df = ak.stock_balance_sheet_by_report_em(symbol=symbol_em)
                self._write_cache(cache_key, raw_df)
            except Exception as e:
                print(f"  Warning: failed to fetch balance sheet: {e}", file=sys.stderr)
                raw_df = pd.DataFrame()

        section_label = "4P. 母公司资产负债表" if report_type == "6" else "4. 合并资产负债表"
        lines = [format_header(2, section_label), ""]
        if raw_df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        df = pd.DataFrame()
        df["ts_code"] = ts_code
        df["end_date"] = raw_df["REPORT_DATE"].astype(str).str[:10].str.replace("-", "")
        df["report_type"] = report_type

        mapping = {
            "money_cap": "MONETARYFUNDS",
            "trad_asset": "TRADE_FINASSET",
            "notes_receiv": "NOTE_RECEIVABLE",
            "accounts_receiv": "ACCOUNTS_RECE",
            "oth_receiv": "OTHER_RECE",
            "inventories": "INVENTORY",
            "oth_cur_assets": "OTHER_CURRENT_ASSET",
            "total_cur_assets": "TOTAL_CURRENT_ASSETS",
            "lt_eqt_invest": "LONG_EQUITY_INVEST",
            "fix_assets": "FIXED_ASSET",
            "cip": "CIP",
            "intang_assets": "INTANGIBLE_ASSET",
            "goodwill": "GOODWILL",
            "total_assets": "TOTAL_ASSETS",
            "st_borr": "SHORT_LOAN",
            "notes_payable": "NOTE_PAYABLE",
            "acct_payable": "ACCOUNTS_PAYABLE",
            "contract_liab": "CONTRACT_LIAB",
            "adv_receipts": "ADVANCE_RECEIVE",
            "payroll_payable": "STAFF_SALARY_PAYABLE",
            "taxes_payable": "TAX_PAYABLE",
            "oth_payable": "OTHER_PAYABLE",
            "non_cur_liab_due_1y": "NONCURRENT_LIAB_1YEAR",
            "oth_cur_liab": "OTHER_CURRENT_LIAB",
            "total_cur_liab": "TOTAL_CURRENT_LIAB",
            "lt_borr": "LONG_LOAN",
            "bond_payable": "BOND_PAYABLE",
            "lt_payable": "LONG_PAYABLE",
            "lease_liab": "LEASE_LIAB",
            "provisions": "PREDICT_LIAB",
            "defer_inc_non_cur_liab": "DEFER_INCOME_NONCURRENT",
            "total_liab": "TOTAL_LIABILITIES",
            "defer_tax_assets": "DEFER_TAX_ASSET",
            "defer_tax_liab": "DEFER_TAX_LIAB",
            "total_hldr_eqy_exc_min_int": "TOTAL_PARENT_EQUITY",
            "minority_int": "MINORITY_EQUITY",
        }
        for ts_col, em_col in mapping.items():
            if em_col in raw_df.columns:
                df[ts_col] = pd.to_numeric(raw_df[em_col], errors="coerce")
            else:
                df[ts_col] = 0.0

        df, years = self._prepare_display_periods(df)
        store_key = "balance_sheet_parent" if report_type == "6" else "balance_sheet"
        self._store[store_key] = df
        self._store[store_key + "_years"] = years

        if not years:
            lines.append("无年报数据\n")
            return "\n".join(lines)

        fields = [
            ("货币资金", "money_cap"),
            ("交易性金融资产", "trad_asset"),
            ("应收票据", "notes_receiv"),
            ("应收账款", "accounts_receiv"),
            ("其他应收款", "oth_receiv"),
            ("存货", "inventories"),
            ("其他流动资产", "oth_cur_assets"),
            ("流动资产合计", "total_cur_assets"),
            ("长期股权投资", "lt_eqt_invest"),
            ("固定资产", "fix_assets"),
            ("在建工程", "cip"),
            ("无形资产", "intang_assets"),
            ("商誉", "goodwill"),
            ("总资产", "total_assets"),
            ("短期借款", "st_borr"),
            ("应付票据", "notes_payable"),
            ("应付账款", "acct_payable"),
            ("合同负债", "contract_liab"),
            ("预收款项", "adv_receipts"),
            ("应付职工薪酬", "payroll_payable"),
            ("应交税费", "taxes_payable"),
            ("其他应付款", "oth_payable"),
            ("一年内到期非流动负债", "non_cur_liab_due_1y"),
            ("其他流动负债", "oth_cur_liab"),
            ("流动负债合计", "total_cur_liab"),
            ("长期借款", "lt_borr"),
            ("应付债券", "bond_payable"),
            ("长期应付款", "lt_payable"),
            ("租赁负债", "lease_liab"),
            ("预计负债", "provisions"),
            ("递延收益", "defer_inc_non_cur_liab"),
            ("总负债", "total_liab"),
            ("递延所得税资产", "defer_tax_assets"),
            ("递延所得税负债", "defer_tax_liab"),
            ("归母所有者权益", "total_hldr_eqy_exc_min_int"),
            ("少数股东权益", "minority_int"),
        ]

        headers = ["项目 (百万元)"] + years
        rows = []
        for label, col in fields:
            row = [label]
            for _, r in df.iterrows():
                val = r.get(col)
                row.append(format_number(val))
            rows.append(row)

        table = format_table(headers, rows, alignments=["l"] + ["r"] * len(years))
        lines.append(table)
        lines.append("\n*单位: 百万元*")
        return "\n".join(lines)

    def _get_balance_sheet_hk(self, ts_code: str, raw_code: str) -> str:
        lines = [format_header(2, "4. 合并资产负债表"), ""]
        cache_key = f"hk_bs_all_{raw_code}"
        raw_df = self._read_cache(cache_key)
        if raw_df is None:
            try:
                raw_df = ak.stock_financial_hk_report_em(stock=raw_code, symbol="资产负债表", indicator="报告期")
                self._write_cache(cache_key, raw_df)
            except Exception as e:
                print(f"  Warning: failed to fetch HK balance sheet: {e}", file=sys.stderr)
                raw_df = pd.DataFrame()

        if raw_df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        rev_map = {v: k for k, v in HK_BALANCE_MAP.items()}
        rev_map["合同负债"] = "contract_liab"
        records = []
        for date, grp in raw_df.groupby("REPORT_DATE"):
            end_date = str(date)[:10].replace("-", "")
            rec = {"ts_code": ts_code, "end_date": end_date, "report_type": "1"}
            for _, r in grp.iterrows():
                item = r["STD_ITEM_NAME"]
                if item in rev_map:
                    col = rev_map[item]
                    rec[col] = float(r["AMOUNT"]) if r["AMOUNT"] is not None and not pd.isna(r["AMOUNT"]) else 0.0
            records.append(rec)

        pivoted = pd.DataFrame(records).sort_values("end_date", ascending=False)
        pivoted, years = self._prepare_display_periods(pivoted)
        self._store["balance_sheet"] = pivoted
        self._store["balance_sheet_years"] = years

        fields = [
            ("现金及等价物", "money_cap"),
            ("应收帐款", "accounts_receiv"),
            ("存货", "inventories"),
            ("流动资产合计", "total_cur_assets"),
            ("联营公司权益", "lt_eqt_invest"),
            ("物业厂房及设备", "fix_assets"),
            ("无形资产", "intang_assets"),
            ("总资产", "total_assets"),
            ("应付帐款", "acct_payable"),
            ("递延收入(流动)", "contract_liab"),
            ("短期贷款", "st_borr"),
            ("流动负债合计", "total_cur_liab"),
            ("长期贷款", "lt_borr"),
            ("总负债", "total_liab"),
            ("递延税项资产", "defer_tax_assets"),
            ("递延税项负债", "defer_tax_liab"),
            ("股东权益", "total_hldr_eqy_exc_min_int"),
            ("少数股东权益", "minority_int"),
        ]

        headers = ["项目 (百万港元)"] + years
        rows = []
        for label, col in fields:
            row = [label]
            for _, r in pivoted.iterrows():
                val = r.get(col)
                row.append(format_number(val))
            rows.append(row)

        table = format_table(headers, rows, alignments=["l"] + ["r"] * len(years))
        lines.append(table)
        lines.append("\n*单位: 百万港元*")
        return "\n".join(lines)

    def get_balance_sheet_parent(self, ts_code: str) -> str:
        return self.get_balance_sheet(ts_code, report_type="6")

    # --- Section 5: Cash Flow Statement ---

    def get_cashflow(self, ts_code: str) -> str:
        ts_code, raw_code, mkt = normalize_code(ts_code)
        if self._is_hk(ts_code):
            return self._get_cashflow_hk(ts_code, raw_code)

        symbol_em = f"{mkt}{raw_code}"
        cache_key = f"cf_{symbol_em}"
        raw_df = self._read_cache(cache_key, max_age_days=7)
        if raw_df is None:
            try:
                raw_df = ak.stock_cash_flow_sheet_by_report_em(symbol=symbol_em)
                self._write_cache(cache_key, raw_df)
            except Exception as e:
                print(f"  Warning: failed to fetch cash flow: {e}", file=sys.stderr)
                raw_df = pd.DataFrame()

        lines = [format_header(2, "5. 现金流量表"), ""]
        if raw_df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        df = pd.DataFrame()
        df["ts_code"] = ts_code
        df["end_date"] = raw_df["REPORT_DATE"].astype(str).str[:10].str.replace("-", "")
        df["report_type"] = "1"

        mapping = {
            "n_cashflow_act": "NETCASH_OPERATE",
            "n_cashflow_inv_act": "NETCASH_INVEST",
            "n_cash_flows_fnc_act": "NETCASH_FINANCE",
            "c_pay_acq_const_fiolta": "CONSTRUCT_LONG_ASSET",
            "depr_fa_coga_dpba": "FA_IR_DEPR",
            "amort_intang_assets": "IA_AMORTIZE",
            "lt_amort_deferred_exp": "LPE_AMORTIZE",
            "c_pay_dist_dpcp_int_exp": "ASSIGN_DIVIDEND_PORFIT",
            "c_pay_to_staff": "PAY_STAFF_CASH",
            "c_paid_for_taxes": "PAY_ALL_TAX",
            "n_recp_disp_fiolta": "DISPOSAL_LONG_ASSET",
            "receiv_tax_refund": "REFUND_TAX",
            "c_recp_return_invest": "INVEST_WITHDRAW_CASH",
        }
        for ts_col, em_col in mapping.items():
            if em_col in raw_df.columns:
                df[ts_col] = pd.to_numeric(raw_df[em_col], errors="coerce")
            else:
                df[ts_col] = 0.0

        # Calculate FCF: n_cashflow_act - abs(c_pay_acq_const_fiolta)
        df["fcf"] = df["n_cashflow_act"] - df["c_pay_acq_const_fiolta"].abs()

        df, years = self._prepare_display_periods(df)
        self._store["cashflow"] = df
        self._store["cashflow_years"] = years

        if not years:
            lines.append("无年报数据\n")
            return "\n".join(lines)

        simple_fields = [
            ("经营活动现金流 (OCF)", "n_cashflow_act"),
            ("投资活动现金流", "n_cashflow_inv_act"),
            ("筹资活动现金流", "n_cash_flows_fnc_act"),
            ("资本支出(购建固定资产等)", "c_pay_acq_const_fiolta"),
            ("支付给职工现金", "c_pay_to_staff"),
            ("支付的各项税费", "c_paid_for_taxes"),
            ("处置固定资产收回现金", "n_recp_disp_fiolta"),
            ("收到税费返还", "receiv_tax_refund"),
            ("取得投资收益收到现金", "c_recp_return_invest"),
            ("分配股利偿付利息", "c_pay_dist_dpcp_int_exp"),
        ]

        headers = ["项目 (百万元)"] + years
        rows = []
        for label, col in simple_fields:
            row = [label]
            for _, r in df.iterrows():
                val = r.get(col)
                row.append(format_number(val))
            rows.append(row)

        da_row = ["折旧与摊销 (D&A)"]
        for _, r in df.iterrows():
            depr = r.get("depr_fa_coga_dpba")
            amort_intang = r.get("amort_intang_assets")
            amort_deferred = r.get("lt_amort_deferred_exp")
            vals = [v for v in [depr, amort_intang, amort_deferred]
                    if v is not None and not pd.isna(v) and v > 0]
            if vals:
                da_row.append(format_number(sum(float(v) for v in vals)))
            else:
                da_row.append("—")
        rows.append(da_row)

        fcf_row = ["自由现金流 (FCF)"]
        for _, r in df.iterrows():
            val = r.get("fcf")
            fcf_row.append(format_number(val))
        rows.append(fcf_row)

        table = format_table(headers, rows, alignments=["l"] + ["r"] * len(years))
        lines.append(table)
        lines.append("\n*单位: 百万元; FCF = OCF - |Capex|*")
        return "\n".join(lines)

    def _get_cashflow_hk(self, ts_code: str, raw_code: str) -> str:
        lines = [format_header(2, "5. 现金流量表"), ""]
        cache_key = f"hk_cf_all_{raw_code}"
        raw_df = self._read_cache(cache_key)
        if raw_df is None:
            try:
                raw_df = ak.stock_financial_hk_report_em(stock=raw_code, symbol="现金流量表", indicator="报告期")
                self._write_cache(cache_key, raw_df)
            except Exception as e:
                print(f"  Warning: failed to fetch HK cash flow: {e}", file=sys.stderr)
                raw_df = pd.DataFrame()

        if raw_df.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        rev_map = {v: k for k, v in HK_CASHFLOW_MAP.items()}
        rev_map["加:折旧及摊销"] = "depr_fa_coga_dpba"
        rev_map["购建固定资产"] = "c_pay_acq_const_fiolta"
        records = []
        for date, grp in raw_df.groupby("REPORT_DATE"):
            end_date = str(date)[:10].replace("-", "")
            rec = {"ts_code": ts_code, "end_date": end_date, "report_type": "1"}
            for _, r in grp.iterrows():
                item = r["STD_ITEM_NAME"]
                if item in rev_map:
                    col = rev_map[item]
                    rec[col] = float(r["AMOUNT"]) if r["AMOUNT"] is not None and not pd.isna(r["AMOUNT"]) else 0.0
            records.append(rec)

        pivoted = pd.DataFrame(records).sort_values("end_date", ascending=False)
        ocf = pd.to_numeric(pivoted["n_cashflow_act"] if "n_cashflow_act" in pivoted.columns else 0.0, errors="coerce").fillna(0.0)
        capex = pd.to_numeric(pivoted["c_pay_acq_const_fiolta"] if "c_pay_acq_const_fiolta" in pivoted.columns else 0.0, errors="coerce").fillna(0.0).abs()
        pivoted["fcf"] = ocf - capex
        pivoted, years = self._prepare_display_periods(pivoted)
        self._store["cashflow"] = pivoted
        self._store["cashflow_years"] = years

        fields = [
            ("经营业务现金净额 (OCF)", "n_cashflow_act"),
            ("已付税项", "c_paid_for_taxes"),
            ("投资业务现金净额", "n_cashflow_inv_act"),
            ("购建固定资产等", "c_pay_acq_const_fiolta"),
            ("折旧及摊销 (D&A)", "depr_fa_coga_dpba"),
            ("融资业务现金净额", "n_cash_flows_fnc_act"),
            ("已付股息(融资)", "c_pay_dist_dpcp_int_exp"),
            ("自由现金流 (FCF)", "fcf"),
        ]

        headers = ["项目 (百万港元)"] + years
        rows = []
        for label, col in fields:
            row = [label]
            for _, r in pivoted.iterrows():
                val = r.get(col)
                row.append(format_number(val))
            rows.append(row)

        table = format_table(headers, rows, alignments=["l"] + ["r"] * len(years))
        lines.append(table)
        lines.append("\n*单位: 百万港元; FCF = OCF - |Capex|*")
        return "\n".join(lines)

    # --- Section 6: Dividend History ---

    def get_dividends(self, ts_code: str) -> str:
        ts_code, raw_code, _ = normalize_code(ts_code)
        if self._is_hk(ts_code):
            return self._get_dividends_hk(ts_code, raw_code)

        lines = [format_header(2, "6. 分红历史"), ""]
        cache_key = f"div_{raw_code}"
        raw_df = self._read_cache(cache_key, max_age_days=7)
        if raw_df is None:
            try:
                raw_df = ak.stock_fhps_detail_em(symbol=raw_code)
                self._write_cache(cache_key, raw_df)
            except Exception as e:
                print(f"  Warning: failed to fetch dividends: {e}", file=sys.stderr)
                raw_df = pd.DataFrame()

        if raw_df.empty:
            lines.append("无分红历史数据\n")
            return "\n".join(lines)

        raw_df = raw_df.sort_values("报告期", ascending=False).reset_index(drop=True)

        records = []
        for _, r in raw_df.head(10).iterrows():
            end_date = str(r.get("报告期", ""))[:10].replace("-", "")
            dps_10 = float(r.get("现金分红-现金分红比例", 0) or 0)
            dps = dps_10 / 10.0
            stk_div = float(r.get("送转股份-送转总比例", 0) or 0) / 10.0
            reg_date = str(r.get("股权登记日", "") or "—")[:10].replace("-", "")
            ex_date = str(r.get("除权除息日", "") or "—")[:10].replace("-", "")
            
            total_div_mn = (dps * self._total_shares / 1e6) if self._total_shares > 0 else 0.0

            records.append({
                "ts_code": ts_code,
                "end_date": end_date,
                "cash_div_tax": dps,
                "stk_div": stk_div if stk_div > 0 else None,
                "record_date": reg_date,
                "ex_date": ex_date,
                "div_proc": str(r.get("方案进度", "")),
                "total_div": total_div_mn,
            })

        div_df = pd.DataFrame(records)
        self._store["dividends"] = div_df

        headers = ["年度", "每股现金分红(税前)", "每股送股", "登记日", "除权日", "总分红 (百万元)"]
        rows = []
        for _, r in div_df.iterrows():
            year = r["end_date"][:4] if len(r["end_date"]) >= 4 else "—"
            dps = f"{r['cash_div_tax']:.4f}" if r["cash_div_tax"] else "—"
            stk = f"{r['stk_div']:.4f}" if r["stk_div"] else "—"
            tot = format_number(r["total_div"] * 1e6) if r["total_div"] else "—"
            rows.append([year, dps, stk, r["record_date"], r["ex_date"], tot])

        table = format_table(headers, rows, alignments=["l", "r", "r", "c", "c", "r"])
        lines.append(table)
        return "\n".join(lines)

    def _get_dividends_hk(self, ts_code: str, raw_code: str) -> str:
        lines = [format_header(2, "6. 分红历史"), ""]
        records = []
        if self._yf_available:
            try:
                yf_sym = f"{raw_code.lstrip('0').zfill(4)}.HK"
                t = yf.Ticker(yf_sym)
                divs = t.dividends
                if not divs.empty:
                    divs = divs.sort_index(ascending=False)
                    for dt, val in divs.head(10).items():
                        # In HKEX, annual final dividends go ex-div in H1 of the following year (Y+1 for FY Y)
                        fy_year = str(dt.year - 1 if dt.month <= 8 else dt.year)
                        dps = float(val)
                        tot_mn = (dps * self._total_shares / 1e6) if self._total_shares > 0 else 0.0
                        records.append({
                            "ts_code": ts_code,
                            "end_date": f"{fy_year}1231",
                            "cash_div_tax": dps,
                            "stk_div": None,
                            "record_date": dt.strftime("%Y%m%d"),
                            "ex_date": dt.strftime("%Y%m%d"),
                            "div_proc": "实施",
                            "total_div": tot_mn,
                        })
            except Exception:
                pass

        if not records:
            lines.append("暂无港股分红数据\n")
            return "\n".join(lines)

        div_df = pd.DataFrame(records)
        self._store["dividends"] = div_df

        headers = ["财年", "每股现金分红(HKD)", "除权日", "总分红 (百万港元)"]
        rows = []
        for _, r in div_df.iterrows():
            year = r["end_date"][:4]
            dps = f"{r['cash_div_tax']:.4f}"
            tot = format_number(r["total_div"] * 1e6) if r["total_div"] else "—"
            rows.append([year, dps, r["ex_date"], tot])

        table = format_table(headers, rows, alignments=["l", "r", "c", "r"])
        lines.append(table)
        return "\n".join(lines)

    # --- Section 7: Shareholders ---

    def get_holders(self, ts_code: str) -> str:
        ts_code, raw_code, mkt = normalize_code(ts_code)
        lines = [format_header(2, "7. 股东与治理"), ""]
        if self._is_hk(ts_code):
            lines.append("*[§7 港股主要股东与治理信息待 Agent WebSearch 补充]*\n")
            return "\n".join(lines)

        try:
            df_gdr = ak.stock_gdfx_top_10_em(symbol=f"{mkt.lower()}{raw_code}")
            if not df_gdr.empty:
                headers = ["名次", "股东名称", "持股数", "持股比例(%)"]
                rows = []
                for _, r in df_gdr.head(10).iterrows():
                    rows.append([
                        str(r.get("名次", "")),
                        str(r.get("股东名称", "")),
                        f"{float(r.get('持股数', 0)):,.0f}",
                        f"{float(r.get('占总股本持股比例', 0)):.2f}%",
                    ])
                table = format_table(headers, rows, alignments=["c", "l", "r", "r"])
                lines.append(table)
                return "\n".join(lines)
        except Exception:
            pass
        lines.append("股东数据暂缺\n")
        return "\n".join(lines)

    # --- Section 9: Business Segments ---

    def get_segments(self, ts_code: str) -> str:
        ts_code, raw_code, mkt = normalize_code(ts_code)
        lines = [format_header(2, "9. 主营业务构成"), ""]
        if self._is_hk(ts_code):
            lines.append("*[§9 港股业务分部构成待 Agent WebSearch 补充]*\n")
            return "\n".join(lines)

        try:
            df_zy = ak.stock_zygc_em(symbol=f"{mkt}{raw_code}")
            if not df_zy.empty:
                df_prod = df_zy[df_zy["分类类型"] == "按产品分类"].head(10)
                if not df_prod.empty:
                    headers = ["报告日期", "产品名称", "主营收入 (百万元)", "收入比例(%)", "毛利率(%)"]
                    rows = []
                    for _, r in df_prod.iterrows():
                        rev = float(r.get("主营收入", 0) or 0) / 1e6
                        ratio = float(r.get("收入比例", 0) or 0) * 100
                        margin = float(r.get("毛利率", 0) or 0) * 100
                        rows.append([
                            str(r.get("报告日期", ""))[:10],
                            str(r.get("主营构成", "")),
                            f"{rev:,.2f}",
                            f"{ratio:.2f}%",
                            f"{margin:.2f}%",
                        ])
                    table = format_table(headers, rows, alignments=["c", "l", "r", "r", "r"])
                    lines.append(table)
                    return "\n".join(lines)
        except Exception:
            pass
        lines.append("主营业务构成数据暂缺\n")
        return "\n".join(lines)

    # --- Section 11: Ten-year weekly prices ---

    def get_weekly_prices(self, ts_code: str) -> str:
        ts_code, raw_code, mkt = normalize_code(ts_code)
        lines = [format_header(2, "11. 十年周线行情"), ""]

        cache_key = f"weekly_10y_{raw_code}"
        df = self._read_cache(cache_key, max_age_days=1)
        if df is None:
            if self._is_hk(ts_code):
                if self._yf_available:
                    try:
                        yf_sym = f"{raw_code.lstrip('0').zfill(4)}.HK"
                        t = yf.Ticker(yf_sym)
                        hist = t.history(period="10y", interval="1wk")
                        if not hist.empty:
                            hist = hist.reset_index()
                            hist["trade_date"] = pd.to_datetime(hist["Date"]).dt.strftime("%Y%m%d")
                            hist["ts_code"] = ts_code
                            df = hist.rename(columns={"Close": "close", "High": "high", "Low": "low", "Open": "open"})
                            self._write_cache(cache_key, df)
                    except Exception:
                        df = pd.DataFrame()
            else:
                start_date = (pd.Timestamp.now() - pd.DateOffset(years=10)).strftime("%Y%m%d")
                end_date = pd.Timestamp.now().strftime("%Y%m%d")
                try:
                    daily_df = ak.stock_zh_a_daily(symbol=f"{mkt.lower()}{raw_code}", start_date=start_date, end_date=end_date)
                    if not daily_df.empty:
                        daily_df["date"] = pd.to_datetime(daily_df["date"])
                        daily_df.set_index("date", inplace=True)
                        weekly = daily_df.resample("W-FRI").agg({
                            "open": "first",
                            "high": "max",
                            "low": "min",
                            "close": "last",
                            "volume": "sum" if "volume" in daily_df.columns else "first",
                            "amount": "sum" if "amount" in daily_df.columns else "first",
                        }).dropna()
                        weekly.reset_index(inplace=True)
                        weekly["trade_date"] = weekly["date"].dt.strftime("%Y%m%d")
                        weekly["ts_code"] = ts_code
                        df = weekly
                        self._write_cache(cache_key, df)
                except Exception as e:
                    print(f"  Warning: failed to build weekly prices: {e}", file=sys.stderr)
                    df = pd.DataFrame()

        if df is not None and not df.empty:
            df = df.sort_values("trade_date", ascending=False).reset_index(drop=True)
            self._store["weekly_prices"] = df
            min_10y = df["close"].min()
            max_10y = df["close"].max()
            lines.append(f"- 10年周收盘最低价: **{min_10y:.2f}** 元")
            lines.append(f"- 10年周收盘最高价: **{max_10y:.2f}** 元")
            lines.append(f"- 数据点数: {len(df)} 周\n")
            return "\n".join(lines)

        lines.append("数据缺失\n")
        return "\n".join(lines)

    # --- Section 12: Key Financial Indicators ---

    def get_fina_indicators(self, ts_code: str) -> str:
        ts_code, raw_code, _ = normalize_code(ts_code)
        if self._is_hk(ts_code):
            return self._get_fina_indicators_hk(ts_code, raw_code)

        lines = [format_header(2, "12. 关键财务指标"), ""]
        cache_key = f"abs_{raw_code}"
        df_abs = self._read_cache(cache_key)
        if df_abs is None:
            try:
                df_abs = ak.stock_financial_abstract(symbol=raw_code)
                self._write_cache(cache_key, df_abs)
            except Exception:
                df_abs = pd.DataFrame()

        if df_abs.empty:
            lines.append("数据缺失\n")
            return "\n".join(lines)

        periods = [c for c in df_abs.columns if c.isdigit()]
        periods = sorted(periods, reverse=True)[:10]

        records = []
        for p in periods:
            rec = {"ts_code": ts_code, "end_date": p}
            for ind_name, target_col in [
                ("净资产收益率(ROE)", "roe"),
                ("毛利率", "grossprofit_margin"),
                ("销售净利率", "netprofit_margin"),
                ("资产负债率", "debt_to_assets"),
                ("基本每股收益", "eps"),
                ("每股净资产", "bps"),
                ("每股经营现金流", "ocfps"),
            ]:
                sub = df_abs[df_abs["指标"] == ind_name]
                if not sub.empty and p in sub.columns:
                    val = sub[p].values[0]
                    rec[target_col] = float(val) if val is not None and not pd.isna(val) else None
                else:
                    rec[target_col] = None
            records.append(rec)

        ind_df = pd.DataFrame(records)
        ind_df, years = self._prepare_display_periods(ind_df)
        self._store["fina_indicators"] = ind_df
        self._store["fina_indicators_years"] = years

        fields = [
            ("ROE (%)", "roe"),
            ("毛利率 (%)", "grossprofit_margin"),
            ("净利率 (%)", "netprofit_margin"),
            ("资产负债率 (%)", "debt_to_assets"),
            ("每股经营现金流", "ocfps"),
            ("每股净资产", "bps"),
        ]

        headers = ["指标"] + years
        rows = []
        for label, col in fields:
            row = [label]
            for _, r in ind_df.iterrows():
                val = r.get(col)
                row.append(f"{val:.2f}" if val is not None and not pd.isna(val) else "—")
            rows.append(row)

        table = format_table(headers, rows, alignments=["l"] + ["r"] * len(years))
        lines.append(table)
        return "\n".join(lines)

    def _get_fina_indicators_hk(self, ts_code: str, raw_code: str) -> str:
        lines = [format_header(2, "12. 关键财务指标"), ""]
        try:
            df_ind = ak.stock_financial_hk_analysis_indicator_em(symbol=raw_code, indicator="报告期")
            if not df_ind.empty:
                df_ind["end_date"] = df_ind["REPORT_DATE"].astype(str).str[:10].str.replace("-", "")
                df_ind["roe"] = pd.to_numeric(df_ind["ROE_YEARLY"], errors="coerce")
                df_ind["grossprofit_margin"] = pd.to_numeric(df_ind["GROSS_PROFIT_RATIO"], errors="coerce")
                df_ind["netprofit_margin"] = pd.to_numeric(df_ind["NET_PROFIT_RATIO"], errors="coerce")
                df_ind["debt_to_assets"] = pd.to_numeric(df_ind["DEBT_ASSET_RATIO"], errors="coerce")
                df_ind["bps"] = pd.to_numeric(df_ind["BPS"], errors="coerce")
                df_ind["ocfps"] = pd.to_numeric(df_ind["PER_NETCASH_OPERATE"], errors="coerce")

                df_ind, years = self._prepare_display_periods(df_ind)
                self._store["fina_indicators"] = df_ind
                self._store["fina_indicators_years"] = years

                fields = [
                    ("ROE (%)", "roe"),
                    ("毛利率 (%)", "grossprofit_margin"),
                    ("净利率 (%)", "netprofit_margin"),
                    ("资产负债率 (%)", "debt_to_assets"),
                    ("每股净资产 (HKD)", "bps"),
                ]

                headers = ["指标"] + years
                rows = []
                for label, col in fields:
                    row = [label]
                    for _, r in df_ind.iterrows():
                        val = r.get(col)
                        row.append(f"{val:.2f}" if val is not None and not pd.isna(val) else "—")
                    rows.append(row)

                table = format_table(headers, rows, alignments=["l"] + ["r"] * len(years))
                lines.append(table)
                return "\n".join(lines)
        except Exception as e:
            print(f"  Warning: failed to get HK fina indicators: {e}", file=sys.stderr)

        lines.append("港股财务指标暂缺\n")
        return "\n".join(lines)

    # --- Section 14: Risk-free rate ---

    def get_risk_free_rate(self, ts_code: str) -> str:
        lines = [format_header(2, "14. 无风险利率"), ""]
        if self._is_hk(ts_code):
            rate = 3.25
            trade_date = pd.Timestamp.now().strftime("%Y%m%d")
            rf_df = pd.DataFrame([{
                "trade_date": trade_date,
                "curve_name": "香港外汇基金债券收益率",
                "curve_type": "到期",
                "yield": rate,
            }])
            self._store["risk_free_rate"] = rf_df
            lines.append(f"- 港元/美元基准收益率: **{rate:.2f}%** ({trade_date})")
            lines.append("- 数据来源: 港元无风险利率基准\n")
            return "\n".join(lines)

        rate = 1.70  # fallback
        trade_date = pd.Timestamp.now().strftime("%Y%m%d")

        try:
            df_rate = ak.bond_zh_us_rate()
            if not df_rate.empty and "中国国债收益率10年" in df_rate.columns:
                valid = df_rate["中国国债收益率10年"].dropna()
                if not valid.empty:
                    rate = float(valid.iloc[-1])
                    trade_date = str(df_rate.loc[valid.index[-1], "日期"]).replace("-", "")
        except Exception:
            pass

        rf_df = pd.DataFrame([{
            "trade_date": trade_date,
            "curve_name": "中债国债收益率曲线",
            "curve_type": "到期",
            "yield": rate,
        }])
        self._store["risk_free_rate"] = rf_df

        lines.append(f"- 10年期国债收益率: **{rate:.2f}%** ({trade_date})")
        lines.append("- 数据来源: 中国国债收益率 (AkShare 降级)\n")
        return "\n".join(lines)

    # --- Section 15: Repurchase ---

    def get_repurchase(self, ts_code: str) -> str:
        ts_code, raw_code, _ = normalize_code(ts_code)
        if self._is_hk(ts_code):
            df_rep, md = fetch_hk_repurchase_summary(raw_code, self._unit_label())
            if df_rep is not None:
                self._store["repurchase"] = df_rep
            return md

        lines = [format_header(2, "15. 股票回购"), ""]

        try:
            df_rep = ak.stock_repurchase_em()
            sub = df_rep[df_rep["股票代码"] == raw_code]
            if not sub.empty:
                self._store["repurchase"] = sub
                lines.append(f"- 近期累计回购笔数: {len(sub)}")
                latest = sub.iloc[0]
                lines.append(f"- 最新回购公告: {latest.get('最新公告日期', '—')} (回购金额: {format_number(latest.get('已回购金额', 0))})")
                return "\n".join(lines)
        except Exception:
            pass
        lines.append("近期无重大回购记录\n")
        return "\n".join(lines)

    # --- Section 16: Pledge ---

    def get_pledge_stat(self, ts_code: str) -> str:
        ts_code, raw_code, _ = normalize_code(ts_code)
        if self._is_hk(ts_code):
            return ""
        lines = [format_header(2, "16. 股权质押"), ""]
        try:
            df_p = ak.stock_gpzy_pledge_ratio_em()
            sub = df_p[df_p["股票代码"] == raw_code]
            if not sub.empty:
                self._store["pledge_stat"] = sub
                p_ratio = float(sub.iloc[0].get("质押比例", 0))
                lines.append(f"- 大股东/整体质押比例: **{p_ratio:.2f}%**")
                if p_ratio > 50:
                    lines.append("> ⚠️ 质押比例超过 50%，存在较高平仓风险！")
                else:
                    lines.append("- 质押风险处于可控范围。")
                return "\n".join(lines)
        except Exception:
            pass
        lines.append("质押数据暂缺或质押比例极低\n")
        return "\n".join(lines)

    # --- Assemble complete pack ---

    def assemble_data_pack(self, ts_code: str) -> str:
        ts_code, raw_code, _ = normalize_code(ts_code)
        timestamp = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

        currency = "HKD" if self._is_hk(ts_code) else "CNY"
        self._currency = currency
        unit_label = "百万港元" if currency == "HKD" else "百万元"

        lines = [
            format_header(1, f"数据包 — {ts_code}"),
            "",
            f"*生成时间: {timestamp}*",
            f"*数据来源: AkShare & yfinance (免费降级)*",
            f"*金额单位: {unit_label} (除特殊标注)*",
        ]
        if currency == "HKD":
            lines.append("*报表币种: HKD*")
        lines.extend(["", "---", ""])

        if self._is_hk(ts_code):
            sections = [
                ("1. 基本信息", self.get_basic_info),
                ("2. 市场行情", self.get_market_data),
                ("3. 合并利润表", self.get_income),
                ("4. 合并资产负债表", self.get_balance_sheet),
                ("5. 现金流量表", self.get_cashflow),
                ("6. 分红历史", self.get_dividends),
                ("7. 股东与治理", self.get_holders),
                ("9. 主营业务构成", self.get_segments),
                ("11. 十年周线行情", self.get_weekly_prices),
                ("12. 关键财务指标", self.get_fina_indicators),
                ("14. 无风险利率", self.get_risk_free_rate),
                ("15. 股票回购", self.get_repurchase),
            ]
        else:
            sections = [
                ("1. 基本信息", self.get_basic_info),
                ("2. 市场行情", self.get_market_data),
                ("3. 合并利润表", self.get_income),
                ("3P. 母公司利润表", self.get_income_parent),
                ("4. 合并资产负债表", self.get_balance_sheet),
                ("4P. 母公司资产负债表", self.get_balance_sheet_parent),
                ("5. 现金流量表", self.get_cashflow),
                ("6. 分红历史", self.get_dividends),
                ("7. 股东与治理", self.get_holders),
                ("9. 主营业务构成", self.get_segments),
                ("11. 十年周线行情", self.get_weekly_prices),
                ("12. 关键财务指标", self.get_fina_indicators),
                ("14. 无风险利率", self.get_risk_free_rate),
                ("15. 股票回购", self.get_repurchase),
                ("16. 股权质押", self.get_pledge_stat),
            ]

        completed = 0
        for name, method in sections:
            try:
                print(f"  Collecting {name} via AkShare/yfinance...")
                section_md = method(ts_code)
                if section_md:
                    lines.append(section_md)
                    lines.append("")
                    completed += 1
            except Exception as e:
                print(f"  ⚠️ Error collecting {name}: {e}", file=sys.stderr)
                lines.append(format_header(2, name) + "\n\n数据采集异常\n")

        # §8 & §10 Placeholders
        lines.append(format_header(2, "8. 行业与竞争 (Agent WebSearch)"))
        lines.append("\n*[§8 待Agent WebSearch补充]*\n")
        lines.append(format_header(2, "10. MD&A核心摘要 (Agent WebSearch)"))
        lines.append("\n*[§10 待Agent WebSearch补充]*\n")

        # §13 Warnings
        lines.append(self.warnings.format_warnings())
        lines.append("")

        # §17 Derived Metrics
        try:
            print("  Computing §17 Derived Metrics...")
            metrics_md = self.compute_derived_metrics(ts_code)
            lines.append(metrics_md)
            lines.append("")
            completed += 1
        except Exception as e:
            print(f"  ⚠️ Error computing derived metrics: {e}", file=sys.stderr)
            lines.append(format_header(2, "17. 衍生指标（Python 预计算）") + f"\n\n计算异常: {e}\n")

        lines.append("---")
        total_sec = len(sections) + 2  # plus placeholders & derived
        lines.append(f"*共 {completed}/{total_sec} 个数据板块成功获取*")

        return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Turtle Investment Framework - AkShare Data Collector")
    parser.add_argument("--code", required=True, help="Stock code (e.g., 600887, 000858, 06049.HK)")
    parser.add_argument("--output", default=None, help="Output path (default: output/{code}_{company}/data_pack_market.md)")
    parser.add_argument("--dry-run", action="store_true", help="Print stock info and exit")
    args = parser.parse_args()

    ts_code, raw_code, _ = normalize_code(args.code)
    if args.dry_run:
        print(f"=== Akshare Dry Run ===")
        print(f"  Code: {args.code} -> {ts_code}")
        sys.exit(0)

    client = AkshareClient()
    print(f"Starting AkShare/yfinance collection for {ts_code}...")
    content = client.assemble_data_pack(ts_code)

    company_name = client._company_name or raw_code
    if args.output:
        out_path = args.output
    else:
        out_dir = os.path.join(PROJECT_ROOT, "output", f"{raw_code}_{company_name}")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "data_pack_market.md")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"\n✅ Data pack written to: {out_path}")


if __name__ == "__main__":
    main()
