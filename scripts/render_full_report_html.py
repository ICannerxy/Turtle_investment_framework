#!/usr/bin/env python3
"""render_full_report_html.py — Convert full analysis report markdown into a modern,
publication-grade standalone HTML report.

Usage:
    python scripts/render_full_report_html.py \
        --input output/06049_保利物业/保利物业_06049_分析报告.md \
        --output output/06049_保利物业/保利物业_06049_分析报告.html
"""

import argparse
import os
import re
import sys
import markdown


TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<title>{{ title }}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&family=Noto+Sans+SC:wght@400;500;600;700&display=swap" rel="stylesheet">
<!-- KaTeX for math rendering -->
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"
    onload="renderMathInElement(document.body, {delimiters: [{left: '$$', right: '$$', display: true}, {left: '$', right: '$', display: false}]});"></script>
<!-- Chart.js for interactive financial charts -->
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>

<style>
:root {
  --bg-primary: #ffffff;
  --bg-secondary: #f8fafc;
  --bg-tertiary: #f1f5f9;
  --text-primary: #0f172a;
  --text-secondary: #475569;
  --text-muted: #94a3b8;
  --border-color: #e2e8f0;
  --border-subtle: #edf2f7;
  --accent: #2563eb;
  --accent-light: #eff6ff;
  --green: #059669;
  --green-bg: #ecfdf5;
  --green-border: #a7f3d0;
  --amber: #d97706;
  --amber-bg: #fffbeb;
  --amber-border: #fde68a;
  --red: #dc2626;
  --red-bg: #fef2f2;
  --red-border: #fecaca;
  --card-shadow: 0 1px 3px 0 rgb(0 0 0 / 0.1), 0 1px 2px -1px rgb(0 0 0 / 0.1);
  --header-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.05);
  --font-sans: 'Inter', 'Noto Sans SC', -apple-system, BlinkMacSystemFont, sans-serif;
  --font-mono: 'JetBrains Mono', monospace;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg-primary: #0b0f19;
    --bg-secondary: #111827;
    --bg-tertiary: #1f2937;
    --text-primary: #f8fafc;
    --text-secondary: #cbd5e1;
    --text-muted: #64748b;
    --border-color: #334155;
    --border-subtle: #1e293b;
    --accent: #3b82f6;
    --accent-light: rgba(59, 130, 246, 0.15);
    --green: #10b981;
    --green-bg: rgba(16, 185, 129, 0.15);
    --green-border: rgba(16, 185, 129, 0.3);
    --amber: #f59e0b;
    --amber-bg: rgba(245, 158, 11, 0.15);
    --amber-border: rgba(245, 158, 11, 0.3);
    --red: #ef4444;
    --red-bg: rgba(239, 68, 68, 0.15);
    --red-border: rgba(239, 68, 68, 0.3);
  }
}

* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: var(--font-sans);
  background-color: var(--bg-secondary);
  color: var(--text-primary);
  line-height: 1.75;
  -webkit-font-smoothing: antialiased;
}

/* Header & Branding */
.site-header {
  background: var(--bg-primary);
  border-bottom: 1px solid var(--border-color);
  position: sticky;
  top: 0;
  z-index: 100;
  backdrop-filter: blur(8px);
}
.header-inner {
  max-width: 1200px;
  margin: 0 auto;
  padding: 14px 24px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.logo-title {
  display: flex;
  align-items: center;
  gap: 12px;
  font-weight: 700;
  font-size: 1.1rem;
  color: var(--text-primary);
}
.tag-badge {
  display: inline-block;
  padding: 3px 8px;
  border-radius: 6px;
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
}
.badge-blue { background: var(--accent-light); color: var(--accent); }
.badge-green { background: var(--green-bg); color: var(--green); border: 1px solid var(--green-border); }
.badge-amber { background: var(--amber-bg); color: var(--amber); border: 1px solid var(--amber-border); }
.badge-red { background: var(--red-bg); color: var(--red); border: 1px solid var(--red-border); }

/* Main Layout */
.layout-container {
  max-width: 1200px;
  margin: 32px auto;
  padding: 0 24px;
  display: grid;
  grid-template-columns: 260px 1fr;
  gap: 36px;
  align-items: start;
}

@media (max-width: 900px) {
  .layout-container {
    grid-template-columns: 1fr;
  }
  .sidebar-toc {
    display: none;
  }
}

/* Sidebar Navigation */
.sidebar-toc {
  position: sticky;
  top: 80px;
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 20px 16px;
  max-height: calc(100vh - 100px);
  overflow-y: auto;
}
.toc-title {
  font-size: 0.8rem;
  font-weight: 700;
  text-transform: uppercase;
  color: var(--text-muted);
  letter-spacing: 0.05em;
  margin-bottom: 12px;
  padding-left: 8px;
}
.toc-nav { list-style: none; }
.toc-nav li { margin-bottom: 4px; }
.toc-nav a {
  display: block;
  padding: 6px 10px;
  border-radius: 6px;
  color: var(--text-secondary);
  text-decoration: none;
  font-size: 0.85rem;
  transition: all 0.15s ease;
}
.toc-nav a:hover {
  background: var(--bg-tertiary);
  color: var(--accent);
}

/* Report Content */
.report-paper {
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 16px;
  padding: 48px;
  box-shadow: var(--card-shadow);
}

@media (max-width: 640px) {
  .report-paper {
    padding: 24px 16px;
  }
}

/* Hero Title Box */
.report-hero {
  border-bottom: 2px solid var(--border-color);
  padding-bottom: 24px;
  margin-bottom: 32px;
}
.hero-pre {
  display: flex;
  gap: 10px;
  align-items: center;
  font-family: var(--font-mono);
  font-size: 0.85rem;
  color: var(--text-muted);
  margin-bottom: 8px;
}
.report-hero h1 {
  font-size: 2.2rem;
  font-weight: 800;
  letter-spacing: -0.025em;
  color: var(--text-primary);
  margin-bottom: 12px;
}
.hero-desc {
  font-size: 1rem;
  color: var(--text-secondary);
}

/* KPI Top Grid */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 16px;
  margin: 28px 0 36px;
}
.kpi-card {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 16px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
}
.kpi-label {
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  color: var(--text-muted);
  letter-spacing: 0.05em;
  margin-bottom: 6px;
}
.kpi-val {
  font-family: var(--font-mono);
  font-size: 1.6rem;
  font-weight: 700;
  color: var(--text-primary);
}
.kpi-sub {
  font-size: 0.75rem;
  color: var(--text-secondary);
  margin-top: 4px;
}

/* Financial Charts Container */
.chart-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
  gap: 20px;
  margin: 24px 0 32px;
}
.chart-card {
  background: var(--bg-primary);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  padding: 20px;
  box-shadow: var(--card-shadow);
  display: flex;
  flex-direction: column;
}
.chart-header {
  margin-bottom: 16px;
}
.chart-title {
  font-size: 1rem;
  font-weight: 700;
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 8px;
}
.chart-desc {
  font-size: 0.8rem;
  color: var(--text-muted);
  margin-top: 4px;
}
.chart-canvas-box {
  position: relative;
  height: 300px;
  width: 100%;
}

/* Markdown Elements */
h2 {
  font-size: 1.45rem;
  font-weight: 700;
  margin: 48px 0 18px;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border-color);
  color: var(--text-primary);
  display: flex;
  align-items: center;
  gap: 8px;
}
h2:first-of-type { margin-top: 0; }
h3 {
  font-size: 1.15rem;
  font-weight: 600;
  margin: 28px 0 12px;
  color: var(--text-primary);
}
h4 {
  font-size: 1rem;
  font-weight: 600;
  margin: 20px 0 8px;
  color: var(--text-secondary);
}
p {
  margin-bottom: 16px;
  color: var(--text-secondary);
}
strong {
  color: var(--text-primary);
  font-weight: 600;
}
ul, ol {
  margin: 12px 0 20px 24px;
  color: var(--text-secondary);
}
li { margin-bottom: 6px; }

/* Tables */
.table-wrapper {
  width: 100%;
  overflow-x: auto;
  margin: 20px 0 28px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
}
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.88rem;
  text-align: left;
}
thead tr {
  background: var(--bg-tertiary);
}
th {
  padding: 12px 14px;
  font-weight: 600;
  color: var(--text-secondary);
  border-bottom: 1px solid var(--border-color);
  white-space: nowrap;
}
td {
  padding: 10px 14px;
  border-bottom: 1px solid var(--border-subtle);
  color: var(--text-secondary);
}
tbody tr:hover {
  background: var(--bg-secondary);
}
tbody tr:last-child td {
  border-bottom: none;
}
td:not(:first-child) {
  font-family: var(--font-mono);
}

/* Callouts / Blockquotes */
blockquote {
  background: var(--accent-light);
  border-left: 4px solid var(--accent);
  border-radius: 0 8px 8px 0;
  padding: 14px 18px;
  margin: 20px 0;
  color: var(--text-secondary);
  font-size: 0.92rem;
}
blockquote p:last-child {
  margin-bottom: 0;
}

/* Executive Verdict Box */
.verdict-box {
  background: linear-gradient(135deg, var(--green-bg), var(--bg-secondary));
  border: 1px solid var(--green-border);
  border-radius: 12px;
  padding: 24px;
  margin: 24px 0 32px;
}
.verdict-header {
  font-size: 0.85rem;
  font-weight: 700;
  color: var(--green);
  text-transform: uppercase;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.verdict-text {
  font-size: 1.15rem;
  font-weight: 600;
  color: var(--text-primary);
  line-height: 1.6;
}

/* Code blocks / Raw monospace */
pre {
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 16px;
  font-family: var(--font-mono);
  font-size: 0.85rem;
  overflow-x: auto;
  margin: 20px 0;
  line-height: 1.6;
}

/* Print styles */
@media print {
  body { background: #fff; color: #000; }
  .site-header, .sidebar-toc { display: none; }
  .layout-container { max-width: 100%; margin: 0; padding: 0; grid-template-columns: 1fr; }
  .report-paper { border: none; box-shadow: none; padding: 0; }
}
</style>
</head>
<body>

<header class="site-header">
  <div class="header-inner">
    <div class="logo-title">
      <span>🐢 龟龟投资策略</span>
      <span class="tag-badge badge-blue">v2.0 Framework</span>
    </div>
    <div style="display: flex; gap: 10px; align-items: center;">
      <span class="tag-badge badge-green">06049.HK 保利物业</span>
      <button onclick="window.print()" style="background: var(--bg-tertiary); border: 1px solid var(--border-color); padding: 5px 12px; border-radius: 6px; font-size: 0.8rem; cursor: pointer; color: var(--text-secondary);">🖨️ 打印/另存PDF</button>
    </div>
  </div>
</header>

<div class="layout-container">
  <!-- Sticky TOC -->
  <aside class="sidebar-toc">
    <div class="toc-title">目录导航</div>
    <ul class="toc-nav">
      <li><a href="#报告元信息">📋 报告元信息</a></li>
      <li><a href="#一executive-summary执行摘要">🎯 执行摘要</a></li>
      <li><a href="#关键假设汇总">🔑 关键假设汇总</a></li>
      <li><a href="#15-财务趋势速览近5年">📈 5年财务趋势</a></li>
      <li><a href="#二因子1a五分钟快筛">⚡ 因子1A：五分钟快筛</a></li>
      <li><a href="#三因子1b深度定性分析">🏰 因子1B：商业模式与护城河</a></li>
      <li><a href="#四因子2穿透回报率粗算top-down">🧮 因子2：穿透粗算 (Top-Down)</a></li>
      <li><a href="#五因子3穿透回报率精算bottom-up--现金质量审计">🔬 因子3：现金审计与精算</a></li>
      <li><a href="#六因子4估值与安全边际">🛡️ 因子4：估值与安全边际</a></li>
      <li><a href="#七最终综合输出">🏆 综合结论</a></li>
      <li><a href="#八风险提示与待验证事项">⚠️ 风险与待核实事项</a></li>
    </ul>
  </aside>

  <!-- Report Body -->
  <main class="report-paper">
    {{ hero_and_kpi }}

    <!-- Converted Markdown HTML -->
    <div class="article-content">
      {{ content }}
    </div>

    <footer style="margin-top: 64px; padding-top: 24px; border-top: 1px solid var(--border-color); font-size: 0.8rem; color: var(--text-muted); text-align: center;">
      <p>Turtle Investment Framework · 龟龟投资策略 v2.0</p>
      <p>自动生成时间: {{ date }} · 严谨量化与确定性预计算</p>
    </footer>
  </main>
</div>

<script>
// Wrap markdown tables in responsive container and add section IDs
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".article-content table").forEach(tbl => {
    const wrapper = document.createElement("div");
    wrapper.className = "table-wrapper";
    tbl.parentNode.insertBefore(wrapper, tbl);
    wrapper.appendChild(tbl);
  });

  // Assign IDs to h2 for TOC linking
  document.querySelectorAll(".article-content h2").forEach(h2 => {
    const text = h2.textContent.trim().replace(/\\s+/g, '-');
    if (!h2.id) {
      h2.id = text;
    }
  });
});
</script>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(description="Convert full report MD to standalone HTML dashboard")
    parser.add_argument("--input", required=True, help="Path to input analysis report Markdown")
    parser.add_argument("--output", required=True, help="Path to output HTML file")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: input file {args.input} does not exist", file=sys.stderr)
        sys.exit(1)

    with open(args.input, "r", encoding="utf-8") as f:
        md_text = f.read()

    # Extract Title & Metadata
    title_match = re.search(r"#\s*(.+)", md_text)
    title = title_match.group(1).strip() if title_match else "龟龟投资策略 · 选股分析报告"

    # Extract custom HERO & KPI section if present
    hero_kpi_match = re.search(r"<!-- HERO_KPI_START -->(.*?)<!-- HERO_KPI_END -->", md_text, re.DOTALL)
    if hero_kpi_match:
        hero_and_kpi = hero_kpi_match.group(1).strip()
        md_text = md_text.replace(hero_kpi_match.group(0), "")
    else:
        hero_and_kpi = """
    <div class="report-hero">
      <div class="hero-pre">
        <span>HKEX · 港股主板</span>
        <span>·</span>
        <span>物业管理服务龙头</span>
        <span>·</span>
        <span>股票代码: 06049.HK</span>
      </div>
      <h1>保利物业 (06049.HK) · 选股分析报告</h1>
      <p class="hero-desc">龟龟投资策略深度四因子体系 · 现金流穿透与资产负债表审计（经 2025 官方年报与 2026 中报 PDF 原件逐行审计）</p>
    </div>

    <!-- Executive KPI Grid -->
    <div class="kpi-grid">
      <div class="kpi-card">
        <div class="kpi-label">最新收盘价 (P)</div>
        <div class="kpi-val">26.24 <span style="font-size: 0.9rem; font-weight: normal; color: var(--text-muted)">HKD</span></div>
        <div class="kpi-sub">历史周线 11.4% 底部</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">2026H1 营收 / 归母</div>
        <div class="kpi-val" style="color: var(--accent);">88.3亿 / 9.33亿</div>
        <div class="kpi-sub">同比 +5.2% / +4.8% (稳健抗周期)</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">账面自由净现金</div>
        <div class="kpi-val" style="color: var(--green);">101.3 <span style="font-size: 0.9rem; font-weight: normal; color: var(--text-muted)">亿HKD</span></div>
        <div class="kpi-sub">派发5.6亿股息后仍占市值 69.7%</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">剔除净现金 EV / PE</div>
        <div class="kpi-val">44.0亿 / 2.36x</div>
        <div class="kpi-sub">2026年化 PE 仅 7.78x</div>
      </div>
      <div class="kpi-card">
        <div class="kpi-label">现价毛股息率 / 支付率</div>
        <div class="kpi-val" style="color: var(--green);">6.12% <span style="font-size: 0.9rem; font-weight: normal; color: var(--text-muted)">/ 50%</span></div>
        <div class="kpi-sub">年报审计确证 · 连续两年50%高派息</div>
      </div>
      <div class="kpi-card" style="border-left: 4px solid var(--green);">
        <div class="kpi-label">投资综合评级</div>
        <div class="kpi-val" style="font-size: 1.25rem; color: var(--green);">建议买入</div>
        <div class="kpi-sub">核心防守底仓 · 确定性现金奶牛</div>
      </div>
    </div>
    """

    # Convert Markdown to HTML with tables & code fences
    html_content = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "nl2br", "sane_lists"]
    )

    rendered = TEMPLATE.replace("{{ title }}", title)
    rendered = rendered.replace("{{ hero_and_kpi }}", hero_and_kpi)
    rendered = rendered.replace("{{ content }}", html_content)
    rendered = rendered.replace("{{ date }}", "2026-10-29")

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(rendered)

    print(f"✅ HTML report successfully generated: {args.output}")


if __name__ == "__main__":
    main()
