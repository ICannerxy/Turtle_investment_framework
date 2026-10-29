#!/usr/bin/env python3
"""
财报PDF下载工具 (Financial Report PDF Downloader)

从 stockn.xueqiu.com 或 notice.10jqka.com.cn 下载A股/港股财报PDF文件。
支持年报、中报、一季报、三季报。

Usage:
    python3 scripts/download_report.py \
        --url "https://stockn.xueqiu.com/.../report.pdf" \
        --stock-code SH600887 \
        --report-type 年报 \
        --year 2024 \
        --save-dir .
"""

import argparse
from datetime import date
import json
import os
import re
import sys
import time

import requests

# Exit codes
EXIT_SUCCESS = 0
EXIT_NETWORK_FAILURE = 1
EXIT_PDF_VALIDATION_FAILURE = 2
EXIT_BAD_ARGUMENTS = 3

# Constants
PDF_MAGIC_BYTES = b"%PDF-"
MIN_FILE_SIZE_WARNING = 100 * 1024  # 100KB
DOWNLOAD_TIMEOUT = 120
DEFAULT_MAX_RETRIES = 3
BACKOFF_BASE = 3  # seconds

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/pdf,application/octet-stream,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

URL_PATTERN = re.compile(
    r"^https?://(stockn\.xueqiu\.com|[\w.-]*10jqka\.com\.cn|[\w.-]*cninfo\.com\.cn|[\w.-]*hkexnews\.hk|[\w.-]*dfcfw\.com|[\w.-]*eastmoney\.com|[\w.-]*polywuye\.com|[\w.-]*todayir\.com|[\w.-]*copl\.com\.hk|[\w.-]*chinafoodsltd\.com)/.+\.pdf$",
    re.IGNORECASE,
)


def get_headers(url):
    """Return headers with Referer matching the URL domain."""
    headers = dict(BASE_HEADERS)
    if "cninfo.com.cn" in url:
        headers["Referer"] = "https://www.cninfo.com.cn/"
    elif "10jqka.com.cn" in url:
        headers["Referer"] = "https://10jqka.com.cn/"
    elif "hkexnews.hk" in url:
        headers["Referer"] = "https://www.hkexnews.hk/"
    elif "polywuye.com" in url:
        headers["Referer"] = "https://www.polywuye.com/Investor/notices"
    elif "todayir.com" in url:
        headers["Referer"] = "https://chinaoverseasproperty-staging.todayir.com/"
    elif "copl.com.hk" in url:
        headers["Referer"] = "https://www.copl.com.hk/"
    elif "dfcfw.com" in url or "eastmoney.com" in url:
        headers["Referer"] = "https://data.eastmoney.com/"
    else:
        headers["Referer"] = "https://xueqiu.com/"
    return headers


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Download financial report PDF from cninfo.com.cn, stockn.xueqiu.com, or 10jqka.com.cn"
    )
    parser.add_argument(
        "--url", required=False, help="PDF URL from cninfo.com.cn, stockn.xueqiu.com, or 10jqka.com.cn"
    )
    parser.add_argument(
        "--stock-code", required=True, help="Stock code (e.g. SH600887, 00700, 600600)"
    )
    parser.add_argument(
        "--company", default="", help="Company name (optional, assists in report search)"
    )
    parser.add_argument(
        "--report-type",
        required=False,
        help="Report type (年报/中报/一季报/三季报/annual/interim)",
    )
    parser.add_argument(
        "--year", required=False, help="Report year (e.g. 2024)"
    )
    parser.add_argument(
        "--save-dir", default=".", help="Directory to save the PDF (default: .)"
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=DEFAULT_MAX_RETRIES,
        help=f"Max download retries (default: {DEFAULT_MAX_RETRIES})",
    )
    parser.add_argument(
        "--fetch-all",
        action="store_true",
        help="Auto-fetch complete report package (latest 2 years annual reports + current year Q1, Interim, Q3 reports)",
    )
    args = parser.parse_args(argv)
    if not args.fetch_all and (not args.url or not args.report_type or not args.year):
        if args.stock_code and not args.url:
            args.fetch_all = True
        else:
            parser.error("--url, --report-type, and --year are required unless --fetch-all is specified.")
    return args


def validate_url(url):
    """Validate that the URL points to a supported source and ends with .pdf."""
    if not URL_PATTERN.match(url):
        return False, (
            f"Invalid URL: {url}\n"
            "URL must be a .pdf link from cninfo.com.cn, stockn.xueqiu.com, 10jqka.com.cn, hkexnews.hk, or official IR domains"
        )
    return True, ""


def build_filename(stock_code, report_type, year):
    """Build output filename: {code}_{year}_{report_type}.pdf

    Strips SH/SZ/HK prefix and exchange suffix (.SH/.SZ/.HK) from stock_code to match
    coordinator.md convention (e.g. 600887_2024_年报.pdf, 06078_2025_年报.pdf).
    """
    # Normalize report type
    type_map = {
        "annual": "年报",
        "interim": "中报",
        "q1": "一季报",
        "q3": "三季报",
    }
    normalized = type_map.get(report_type.lower(), report_type)
    # Strip exchange prefix and suffix for filename
    code = re.sub(r"^(SH|SZ|HK)", "", stock_code, flags=re.IGNORECASE).split(".")[0].strip()
    return f"{code}_{year}_{normalized}.pdf"


def download_annual_report(url, save_path, max_retries=DEFAULT_MAX_RETRIES):
    """
    Download PDF with retry and validation.

    Returns:
        tuple: (success: bool, message: str, filesize: int)
    """
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            print(
                f"Downloading (attempt {attempt}/{max_retries}): {url}",
                file=sys.stderr,
            )

            response = requests.get(
                url,
                headers=get_headers(url),
                timeout=DOWNLOAD_TIMEOUT,
                stream=True,
            )
            response.raise_for_status()

            # Check Content-Type
            content_type = response.headers.get("Content-Type", "")
            if "pdf" not in content_type.lower() and "octet-stream" not in content_type.lower():
                print(
                    f"Warning: Content-Type is '{content_type}', expected PDF",
                    file=sys.stderr,
                )

            # Download to temporary path first, then rename
            tmp_path = save_path + ".tmp"
            total_size = 0
            first_chunk = True

            with open(tmp_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        # Validate PDF magic bytes on first chunk
                        if first_chunk:
                            if not chunk[:5].startswith(PDF_MAGIC_BYTES):
                                os.remove(tmp_path)
                                return (
                                    False,
                                    "PDF validation failed: file does not start with %PDF- magic bytes",
                                    0,
                                )
                            first_chunk = False
                        f.write(chunk)
                        total_size += len(chunk)

            # Rename tmp to final
            if os.path.exists(save_path):
                os.remove(save_path)
            os.rename(tmp_path, save_path)

            # Size warning
            if total_size < MIN_FILE_SIZE_WARNING:
                print(
                    f"Warning: file size ({total_size} bytes) is smaller than expected (<100KB)",
                    file=sys.stderr,
                )

            return True, "Download successful", total_size

        except requests.exceptions.RequestException as e:
            last_error = str(e)
            print(
                f"Attempt {attempt} failed: {last_error}", file=sys.stderr
            )
            # Clean up partial download
            tmp_path = save_path + ".tmp"
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

            if attempt < max_retries:
                wait_time = BACKOFF_BASE * attempt  # 3s, 6s, 9s
                print(f"Retrying in {wait_time}s...", file=sys.stderr)
                time.sleep(wait_time)

    return False, f"Download failed after {max_retries} attempts: {last_error}", 0


def print_result(success, filepath="", filesize=0, url="", stock_code="",
                 report_type="", year="", message=""):
    """Print structured result block for Claude to parse."""
    status = "SUCCESS" if success else "FAILED"
    print("\n---RESULT---")
    print(f"status: {status}")
    print(f"filepath: {filepath}")
    print(f"filesize: {filesize}")
    print(f"url: {url}")
    print(f"stock_code: {stock_code}")
    print(f"report_type: {report_type}")
    print(f"year: {year}")
    print(f"message: {message}")
    print("---END---")


def is_hk_code(stock_code: str) -> bool:
    """Check if stock code represents a Hong Kong stock."""
    s = stock_code.upper().strip()
    if s.endswith(".HK") or s.startswith("HK"):
        return True
    clean = re.sub(r"[^\d]", "", s)
    if len(clean) == 5:
        return True
    return False


def _fetch_hkex_candidates(stock_code: str) -> dict:
    """Fetch report candidates from HKEXnews (披露易) for HK stocks.

    Returns dict mapping (year_str, rtype_str) -> (title, full_url)
    """
    clean_digits = re.sub(r"[^\d]", "", stock_code)
    if not clean_digits:
        return {}
    code_5d = f"{int(clean_digits):05d}"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://www.hkexnews.hk/",
    }
    candidates = {}
    try:
        # Step 1: Map 5-digit code to HKEX internal stockId
        r_stock = requests.get(
            "https://www1.hkexnews.hk/ncms/script/eds/activestock_sehk_c.json",
            headers=headers,
            timeout=10,
        )
        stock_list = r_stock.json()
        hk_item = next((item for item in stock_list if item.get("c") == code_5d), None)
        if not hk_item:
            print(f"HKEXnews warning: Stock code {code_5d} not found in active stock list", file=sys.stderr)
            return candidates

        stock_id = hk_item["i"]

        # Step 2: Query titleSearchServlet for financial reports (t1code=40000)
        curr_year = date.today().year
        from_date_hk = f"{curr_year - 3}0101"
        to_date_hk = f"{curr_year + 1}1231"
        search_url = (
            f"https://www1.hkexnews.hk/search/titleSearchServlet.do?"
            f"sortDir=0&sortByOptions=DateTime&category=0&market=SEHK&stockId={stock_id}"
            f"&documentType=-1&fromDate={from_date_hk}&toDate={to_date_hk}&title="
            f"&t1code=40000&t2Gcode=-2&t2code=-2&rowRange=50&lang=zh"
        )
        res = requests.get(search_url, headers=headers, timeout=10)
        data = res.json()
        result_list = json.loads(data.get("result", "[]"))

        for doc in result_list:
            title = doc.get("TITLE", "")
            file_link = doc.get("FILE_LINK", "")
            if not file_link.lower().endswith(".pdf"):
                continue

            # Skip ESG, corporate governance, summary, notices
            if any(skip in title for skip in ["環境", "环境", "ESG", "管治", "摘要", "更正", "澄清", "代表委任", "通函", "通告", "英文", "取消"]):
                continue

            m_year = re.search(r"(20\d{2})", title)
            year = m_year.group(1) if m_year else None
            if not year:
                continue

            rtype = None
            if "年報" in title or "年度報告" in title or "年报" in title:
                rtype = "年报"
            elif "中期" in title or "半年度" in title:
                rtype = "中报"
            elif "第一季度" in title or "一季" in title:
                rtype = "一季报"
            elif "第三季度" in title or "三季" in title:
                rtype = "三季报"

            if rtype:
                key = (year, rtype)
                if key not in candidates:
                    url = "https://www1.hkexnews.hk" + file_link if file_link.startswith("/") else file_link
                    candidates[key] = (title, url)

    except Exception as e:
        print(f"HKEXnews report query warning: {e}", file=sys.stderr)

    return candidates


def fetch_complete_report_pack(stock_code: str, company: str = "", save_dir: str = ".", max_retries: int = DEFAULT_MAX_RETRIES) -> int:
    """Fetch the complete report package:
    1. Latest 2 years annual reports (年报)
    2. Current year Q1 (一季报), Interim (中报), Q3 (三季报)
    """
    os.makedirs(save_dir, exist_ok=True)
    code = re.sub(r"^(SH|SZ|HK)", "", stock_code, flags=re.I).split(".")[0].strip()

    candidates = {}

    is_hk = is_hk_code(stock_code)

    # 1. HK stocks: query HKEXnews (披露易) directly
    if is_hk:
        candidates = _fetch_hkex_candidates(stock_code)

    # 2. A-shares (or fallback for HK stocks if company name is provided): query cninfo.com.cn
    if not candidates:
        search_terms = []
        if is_hk:
            if company:
                search_terms.append(company)
        else:
            search_terms.append(code)
            if company:
                search_terms.append(company)

        if search_terms:
            url_api = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
            headers = {"User-Agent": "Mozilla/5.0"}
            curr_year = date.today().year
            se_date_annual = f"{curr_year - 3}-01-01~{curr_year + 1}-12-31"
            se_date_interim = f"{curr_year - 1}-01-01~{curr_year + 1}-12-31"
            skip_keywords = (
                "摘要", "取消", "更正", "英文", "提示", "说明会",
                "业绩会", "工作函", "回复", "补充", "决议", "保荐", "核查意见", "说明"
            )

            for term in search_terms:
                # 1. Annual reports (latest 2 years)
                try:
                    data_annual = {
                        "pageNum": 1, "pageSize": 15, "column": "", "tabName": "fulltext",
                        "plate": "", "stock": "", "searchkey": f"{term} 年度报告",
                        "category": "category_ndbg_szsh;", "seDate": se_date_annual
                    }
                    r1 = requests.post(url_api, headers=headers, data=data_annual, timeout=10)
                    for ann in (r1.json().get("announcements") or []):
                        t = ann.get("announcementTitle", "")
                        if any(skip in t for skip in skip_keywords):
                            continue
                        if "年度报告" not in t and "年报" not in t:
                            continue
                        m_year = re.search(r"(20\d{2})", t)
                        if m_year:
                            year = m_year.group(1)
                            key = (year, "年报")
                            if key not in candidates:
                                candidates[key] = (t, "http://static.cninfo.com.cn/" + ann.get("adjunctUrl", ""))
                except Exception as e:
                    print(f"Annual report query warning: {e}", file=sys.stderr)

                # 2. Interim and quarterly reports
                try:
                    data_interim = {
                        "pageNum": 1, "pageSize": 20, "column": "", "tabName": "fulltext",
                        "plate": "", "stock": "", "searchkey": f"{term} 报告",
                        "category": "category_bndbg_szsh;category_yjdbg_szsh;category_sjdbg_szsh;", "seDate": se_date_interim
                    }
                    r2 = requests.post(url_api, headers=headers, data=data_interim, timeout=10)
                    for ann in (r2.json().get("announcements") or []):
                        t = ann.get("announcementTitle", "")
                        if any(skip in t for skip in skip_keywords):
                            continue
                        m_year = re.search(r"(20\d{2})", t)
                        if m_year:
                            year = m_year.group(1)
                            rtype = None
                            if "半年度报告" in t or "中报" in t or "中期报告" in t:
                                rtype = "中报"
                            elif "第一季度报告" in t or "一季报" in t:
                                rtype = "一季报"
                            elif "第三季度报告" in t or "三季报" in t:
                                rtype = "三季报"
                            if rtype:
                                key = (year, rtype)
                                if key not in candidates:
                                    candidates[key] = (t, "http://static.cninfo.com.cn/" + ann.get("adjunctUrl", ""))
                except Exception as e:
                    print(f"Interim/Quarterly report query warning: {e}", file=sys.stderr)

                if len(candidates) >= 4:
                    break

    # Select latest 2 annual reports
    annual_keys = sorted([k for k in candidates if k[1] == "年报"], key=lambda x: x[0], reverse=True)[:2]
    # Select current year interim and quarterly reports
    max_year = max([int(k[0]) for k in candidates]) if candidates else date.today().year
    current_year_str = str(max_year)
    order_map = {"一季报": 1, "中报": 2, "三季报": 3}
    interim_keys = sorted(
        [k for k in candidates if k[0] == current_year_str and k[1] in order_map],
        key=lambda x: order_map[x[1]]
    )

    target_keys = annual_keys + interim_keys
    print(f"\n=== Found {len(target_keys)} Target Reports for {stock_code} ===")
    success_count = 0
    for year, rtype in target_keys:
        title, url = candidates[(year, rtype)]
        filename = build_filename(stock_code, rtype, year)
        save_path = os.path.join(save_dir, filename)
        if os.path.exists(save_path) and os.path.getsize(save_path) > MIN_FILE_SIZE_WARNING:
            print(f"  [Exists] {filename} ({os.path.getsize(save_path)} bytes)")
            success_count += 1
            continue
        print(f"  Downloading: {title} -> {filename} ...")
        ok, msg, fsize = download_annual_report(url, save_path, max_retries=max_retries)
        if ok:
            print(f"  ✅ [Downloaded] {filename} ({fsize} bytes)")
            success_count += 1
        else:
            print(f"  ❌ [Failed] {filename}: {msg}", file=sys.stderr)

    return EXIT_SUCCESS if success_count > 0 else EXIT_NETWORK_FAILURE


def main(argv=None):
    args = parse_args(argv)

    if args.fetch_all:
        code = fetch_complete_report_pack(
            stock_code=args.stock_code,
            company=args.company,
            save_dir=args.save_dir,
            max_retries=args.max_retries
        )
        sys.exit(code)

    # Validate URL
    valid, err_msg = validate_url(args.url)
    if not valid:
        print(f"Error: {err_msg}", file=sys.stderr)
        print_result(
            success=False,
            url=args.url,
            stock_code=args.stock_code,
            report_type=args.report_type,
            year=args.year,
            message=err_msg,
        )
        sys.exit(EXIT_BAD_ARGUMENTS)

    # Ensure save directory exists
    os.makedirs(args.save_dir, exist_ok=True)

    # Build filename and full path
    filename = build_filename(args.stock_code, args.report_type, args.year)
    save_path = os.path.join(args.save_dir, filename)

    # Download
    success, message, filesize = download_annual_report(
        url=args.url,
        save_path=save_path,
        max_retries=args.max_retries,
    )

    # Print result
    print_result(
        success=success,
        filepath=os.path.abspath(save_path) if success else "",
        filesize=filesize,
        url=args.url,
        stock_code=args.stock_code,
        report_type=args.report_type,
        year=args.year,
        message=message,
    )

    if not success:
        if "validation" in message.lower():
            sys.exit(EXIT_PDF_VALIDATION_FAILURE)
        else:
            sys.exit(EXIT_NETWORK_FAILURE)

    sys.exit(EXIT_SUCCESS)


if __name__ == "__main__":
    main()
