# 龟龟投资框架 (Turtle Investment Framework) - Agent 指南

> **项目角色**：AI 辅助的 A 股 / 港股 / 美股基本面分析系统。
> **全局准则**：严格遵循全局规范 `~/.codex/AGENTS.md`（极简代码、直击根因、单测验证、拒绝过度设计）。

---

## 1. 核心设计原则与职责边界

本系统采用 **“Python 确定性计算 + LLM 定性/多因子评估”** 的混合架构：

- **Python 端（Scripts）**：负责确定性工作——数据采集（Tushare/Akshare）、缓存、网格预计算（§17.10-13 支付率/GG/G/λ）、行序校正、PDF 附注提取与一致性门槛校验（QC/Report Consistency）。
- **LLM 端（Prompts / Agents）**：负责定性逻辑推导与多因子综合裁定（商业分析 D1-D6、护城河识别）。
- **核心铁律**：
  1. **有网格时不做数学**：LLM 严禁在 Prompt 中自行推导或手算财务乘数，必须基于 Python 预计算的网格或 `computed_metrics.md` 查表选行并说明理由。
  2. **溯源标注**：所有关键财务数字必须带 `[src: ...]` 溯源标注。

---

## 2. 常用开发与测试命令

### 2.1 环境配置
- 项目依赖本地虚拟环境：`.venv/`（Python >= 3.10）
- Python 执行器路径：`.venv/bin/python`
- 环境变量：依赖 `.env` 中的 `TUSHARE_TOKEN`

### 2.2 测试验证（开发后必跑）
严格遵循全局规范：**优先单类/单文件快速测试，禁止无谓全量扫描**。

```bash
# 快速运行单个测试文件
.venv/bin/pytest tests/test_config.py -q --tb=short

# 运行特定测试类或测试函数
.venv/bin/pytest tests/test_derived_metrics_grids.py -k "TestPenetrationGrid" -q --tb=short

# 回归验证（改动核心逻辑后）
.venv/bin/pytest tests/ -x -q --tb=short
```

> **注意**：测试已内置 `tests/fixtures/mock_tushare_responses`，普通单测无需联网与真实 Token，严禁编写消耗在线 Token 的脆弱测试。

### 2.3 常用脚本执行
```bash
# 1. 采集数据（支持 --no-cache / --cache-refresh）
.venv/bin/python scripts/tushare_collector.py --code 600887.SH --output output/data_pack_market.md

# 2. 预计算与质检
.venv/bin/python scripts/quality_control.py --input output/data_pack_market.md

# 3. 跨段一致性门控校验
.venv/bin/python scripts/report_consistency.py --report output/xxx/report.md --gates

# 4. 渲染双格式报告 (HTML)
.venv/bin/python scripts/render_full_report_html.py --input output/xxx/report.md
```

---

## 3. 目录架构导图

- `scripts/`：核心确定性处理引擎
  - `tushare_collector.py`：多市场行情与财务数据抓取（内置 VIP 行序防御与 TTL 缓存）
  - `cache_utils.py`：TTL 磁盘缓存（财务 7 天，行情不缓存）
  - `valuation_engine.py`：独立估值计算模型
  - `quality_control.py` & `report_consistency.py`：报告一致性与数值硬门控审计
- `strategies/`：策略层
  - `turtle/`：龟龟专属四因子、穿透回报率与网格推导逻辑
  - `valuation/`：多方法估值策略模块
- `shared/`：跨策略共享组件
  - `qualitative/`：D1–D6 商业分析（PDF-first 单 Agent 架构）
- `prompts/`：各阶段 LLM 工作流 Prompts
- `tests/`：单测套件与 mock fixtures
- `output/`：报告输出目录（MD + HTML，按 `{code}_{name}` 归档）

---

## 4. 关键避坑与开发禁区

1. **券商 VIP 中继时序反转陷阱**：
   - Tushare 标准接口返回为“最新在前”，但某些券商中继返回为“最旧在前”。
   - 任何涉及价格、市值计算的位置，必须使用已有的防御性排序（`newest-first`），严禁直接用 `df.iloc[0]` 假设是最新值。
2. **不可随意引入重型依赖**：
   - 数据处理优先使用现有 `pandas` / `pyarrow` 方案，保持轻量高效。
3. **保持收敛**：
   - v1 旧版遗留代码集中存放在 `legacy/` 目录下；如需扩展新功能，优先替代旧逻辑并清理冗余分支，不要保留多套并行实现。
4. **单行排版与代码格式**：
   - 严格遵循全局规范：声明与赋值语句同单行紧凑排版，禁止中途折行。
