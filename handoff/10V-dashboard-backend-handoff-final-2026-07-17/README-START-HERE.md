# 10V Dashboard 后端交接包（UI 定稿）

交接日期：2026-07-17

## 先看什么

1. 打开 `01-final-ui/10-market-voices-complete.html`，这是产品确认的唯一 UI 定稿。
2. 阅读 `02-backend-contract/BACKEND-HANDOFF.md`，按其中的 P0 / P1 清单改造后端与正式渲染器。
3. 将正式渲染器输出对齐 `02-backend-contract/dashboard-render-contract.schema.json`。
4. 聚合判断必须复用 `03-rules-and-tests/report_rules.py`，并遵守 `03-rules-and-tests/report-aggregation-rules.md`。
5. 修改规则后运行 `03-rules-and-tests/test_report_rules.py`。

## 文件说明

- `01-final-ui/10-market-voices-complete.html`：可独立移动的单 HTML UI 定稿，包含内嵌头像、一级页面、人物侧栏和股票下钻。当前内容包含演示数据，不能直接当生产数据源。
- `02-backend-contract/BACKEND-HANDOFF.md`：后端差距、字段来源、聚合口径、渲染要求和验收清单。
- `02-backend-contract/dashboard-render-contract.schema.json`：正式渲染器需要生成的数据合同。
- `03-rules-and-tests/`：已确认的确定性规则、Python 实现和测试。
- `04-reference/bloggers.json`：10 个追踪账号及角色配置。
- `04-reference/CHANGE-HANDOFF-FULL.md`：完整 UI 与规则修改历史，仅供追溯；实现以本包的定稿合同和规则为准。
- `VALIDATION.md`：本交接包生成时的验证结果。
- `SHA256SUMS.txt`：交付文件校验值。

## 不可误解的边界

- 正式运行由确定性脚本读取结构化数据库并生成 HTML；LLM 不在渲染时清洗、补写或推断数据。
- `reasons[]` 用于 UI 理由摘要；展开证据使用原始 `text`；二者必须链接同一条 `mention.url`。
- 7 个 `opinion` 账号进入观点共识计算；`flow`、`news`、`disclosure` 是独立信号源，不得伪装为个人观点或计入观点账号共识人数。
- “看多/看空”只描述公开内容产生的结构化方向信号，不代表持仓、买卖、收益、命中率或投资建议。
- 最终生产交付仍必须是单 HTML，不得重新依赖 `assets/` 或独立 `stock-detail.html`。
