# 现有后端到定稿渲染合同的字段映射

此文件帮助在现有项目上增量修改，不要求重建数据库。

| 定稿合同字段 | 现有来源 | 处理 |
|---|---|---|
| `meta.report_date_et` | 渲染参数 | 用 America/New_York 日期，不用机器本地日期 |
| `people[].blogger_id` | `config/bloggers.json: bloggers[].id` | 原样使用稳定 ID |
| `display_name/handle/x_url/signal_type` | `config/bloggers.json` | 原样映射 |
| `avatar_data_uri` | 当前 UI 内嵌头像或正式头像源 | 构建时转 Base64 data URI |
| `instrument.*` | `data/db/stocks/<ticker>.json: instrument` | 原样映射，禁止未知代码默认美股 |
| `evidence.*` | stock 文档 `mentions[]` | 保持同一 mention 的 reasons/text/url 绑定 |
| `daily_stock_lists` | 当日按 blogger + instrument 聚合 | 调用 `tracked_person_stock_lists()`；一级 UI 只渲染 bullish/bearish |
| 日/周主栏目 | 窗口内 opinion explicit stance | 每股调用 `classify_main_section()` |
| 周报变化 | 前窗/当前窗 opinion 账号集合 | 调用 `classify_weekly_change()` 和 `account_change_states()` |
| 月报 `unique_post_count` | 窗口 mentions | URL 去重；缺 URL 才用 tweet_id |
| 月报方向次数 | 窗口有效结构化方向记录 | neutral 单独计数，不进入 bull/bear share 分母 |
| 月报账号集合 | 窗口 mentions | distinct blogger_id，另输出 bullish/bearish 账号集合供悬浮 |
| `price_change` | `price_series` + `price_status` | 取窗口内首末可用交易日；不可用时 percentage=null |
| 下钻 `mention_days` | 28 日 mentions | 按 date 分组，保留完整 evidence |
| 下钻 `person_windows` | 每股 mentions + 10 人 roster | 调用 `person_window_state()`；每窗必须恰好 10 人 |
| 下钻 `default_person_window` | 产品定稿常量 | 固定输出 `today`，首次打开激活“今日” |
| 下钻 `window_summaries` | 每股价格与 mentions | 分别输出今日/7日/28日概览数字和窗口涨跌幅 |
| 下钻 `people_by_window` | 三个窗口内每人每股 mentions | 每窗分别输出次数、构成、最近记录、反转、窗口内 evidence |
| `latest_direction` | 所选窗口按 created_at/date 排序后的最后一条 mention | 只允许 bullish/bearish/neutral/null；不得使用累计状态 both |
| `consistency_*` | 所选窗口 bull/bear 次数 | 调用 `person_stance_consistency()` |

## 现有实现中不要直接复用的部分

- 旧 `serenity_render.py` 的页面模板和旧导航结构不是定稿 UI。
- 旧的自然月、30 日、季度或 90 日概念不能替代固定 28 日窗口。
- 旧“其他值得关注”栏目不得恢复。
- `total_mentions` 和 `total_mentions_by_blogger` 是全历史事实，不能直接当窗口统计。
- mention 的普通 `stance` 字段不能绕过 `signal_type` 与 `mention_type` 资格检查后直接进入观点共识。
- 演示 HTML 中的硬编码股票、人物理由、价格和统计数字不能写回数据库。
