# 迭代方案：领导动态每日采集（v2，2026-05-07）

## 背景

原版本采集"门户网站周报"栏目页，新版本改为每日手动导出各领导的当日动态。

## 数据源

- 领导发现入口：`https://www.hubei.gov.cn/szf/`
- 各领导个人主页：`https://www.hubei.gov.cn/szf/sld/{slug}/index.shtml`
  - 链接格式多样（`//`、`http://`、`https://`），使用子串匹配定位
  - 个人主页 `<img alt="职务：姓名">` 可直接提取姓名和职务
- 各领导活动列表页：个人主页内"政务活动"链接，href 含 `zyhd`/`zwb_hy`/`hyhd` 等关键词
- 列表页分页规律：`index.shtml` → `index_1.shtml` → `index_2.shtml` …
- 列表条目格式：`[标题](url) YYYY-MM-DD HH:MM`，文章链接必须以 `.shtml` 结尾（过滤导航目录）
- **王忠林（省委书记）不在 szf/ 领导列表，自然不采集**

## 目录结构（新）

```
output/领导动态/
└── {year}年_{M}月{D}日/          ← 导出触发日期（今天）
    └── {leader_display}/          ← 如"李殿勋省长"
        └── {activity_date}/       ← 如"4月22日"（从正文第一段提取）
            ├── {title}.docx
            └── {title}.ofd
```

旧目录 `output/门户网站周报/` 保持不变，仍可通过旧 API 访问。

## 活动日期提取规则

从正文第一段开头按优先级匹配：

| 优先级 | 正则模式 | 示例 | 提取结果 |
|--------|---------|------|---------|
| 1 | `(\d+)月(\d+)日至(\d+)月(\d+)日` | 4月30日至5月1日 | 5月1日（跨月区间取结束日） |
| 2 | `(\d+)月(\d+)至(\d+)日` | 4月20至22日 | 4月22日（同月区间取结束日） |
| 3 | `(\d+)月(\d+)日` | 4月20日下午 | 4月20日 |
| 兜底 | 无匹配 | — | pub_dt 的 `M月D日` |

## 导出时间窗口逻辑

- `end_dt` = 当前时刻
- 查 `export_history.json`（key = `YYYY-MM-DD`，value = ISO 时间戳）
- 优先查**今天**的记录（同天多次导出场景），无则查**昨天**，再无则回退到昨天 `00:00:00`
- `start_from` 可由前端手动覆盖（ISO datetime 字符串）
- 导出成功后写入今天的时间戳
- 同天多次导出：跳过已存在的 DOCX/OFD 文件，更新时间戳

## 新增代码

### scraper.py（只新增，不改现有函数）

| 函数 | 签名 | 说明 |
|------|------|------|
| `discover_leaders` | `(szf_url) → list[dict]` | 从 szf/ 自动发现领导列表及 activity_url |
| `_parse_leader_profile` | `(profile_url, base, name_hint) → dict\|None` | 解析单个领导主页，提取姓名、职务、活动URL |
| `parse_leader_activity_list` | `(activity_url, start_dt, end_dt) → list[dict]` | 分页抓取活动列表，按时间窗口过滤 |
| `extract_activity_date` | `(paragraphs, pub_dt) → str` | 从正文第一段提取活动日期字符串 |

### app.py

| 新增内容 | 说明 |
|---------|------|
| `DAILY_OUTPUT_DIR` | `output/领导动态/` 路径常量 |
| `EXPORT_HISTORY_PATH` | `export_history.json` 路径常量 |
| `compute_export_window(start_from=None)` | 计算 `(start_dt, end_dt)`；`start_from` 不为 None 时直接用作起始时间 |
| `save_export_record(end_dt)` | 写入今天导出时间戳 |
| `do_daily_export(start_from=None)` | 后台线程主逻辑，与旧任务共用 `task_lock` |
| `POST /api/daily-export` | 触发每日导出；接受可选 `{"start_from": "ISO字符串"}` 请求体 |
| `GET /api/export-history` | 返回 `export_history.json` 全部内容（`{}` 如无记录）|
| `GET /api/daily-download/<path>` | 下载单个每日动态文件 |
| `GET /api/daily-download-zip/<path>` | 打包下载某次导出目录 |
| `GET /api/reports` | 返回结构改为 `{"weekly": [...], "daily": [...]}` |

### templates/index.html

- 配置区新增"每日导出"按钮，调用 `POST /api/daily-export`
- 文件列表区改为 Tab 切换（每日动态 / 政务周报）
- 新增 Alpine.js 状态：`activeTab`、`dailyReports`、`exportHistory`、`dailyStartFrom`
- 新增方法：`dailyExport()`（携带可选 `start_from`）、`loadExportHistory()`
- 新增计算属性：`lastExportTime`（取最新导出记录）、`computedStartFrom`（手动指定优先，否则自动推算）
- 按钮下方显示「上次导出时间」和「本次起始时间」，提供 `datetime-local` 输入供手动覆盖，有覆盖值时显示「重置」按钮
- 任务结束后自动调用 `loadExportHistory()` 刷新导出历史
- `loadReports()` 适配新响应结构 `{weekly, daily}`

## 不修改的文件

`config.json`、`scheduler.py`

## 开发过程修复的 Bug

| Bug | 原因 | 修复 |
|-----|------|------|
| `discover_leaders` 返回 0 条 | 原代码只匹配 `^/szf/sld/...` 纯路径，页面实际使用 `//`、`http://`、`https://` 三种前缀 | 改为 `re.search` 子串匹配 `/szf/sld/` |
| 活动列表混入导航链接 | "会议•活动"栏目标题的 URL 为目录路径（无 `.shtml` 后缀），被误采集 | 新增 `.shtml` 后缀过滤，最小标题长度从 4 改为 7 |
| 同天第二次导出仍以昨天 0 点为起始 | `save_export_record` 写今天的 key，`compute_export_window` 只查昨天的 key | 优先查今天的 key，再查昨天，最后回退昨天 00:00 |
| `pollStatus` 结束后未刷新导出历史 | 任务完成回调只调用了 `loadReports()`，未更新 `exportHistory` | 补加 `await this.loadExportHistory()` |
| 文后作者署名未清除（多人名如`（杨念明、王馨）`） | 原正则只匹配 ≤8 字且不含顿号 | 增加整段弹出 + 新正则，覆盖多人名（含`、`/`，`分隔）和含"记者"两种形式 |
| `'pair' object is not subscriptable` | jieba `pair` namedtuple 不支持下标访问 | `list(pseg.cut(...))` 改为 `[(w, f) for w, f in pseg.cut(...)]` |
