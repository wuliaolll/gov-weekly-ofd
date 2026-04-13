# 政务周报 OFD 生成器 — 技术方案

## 一、系统架构

```
┌─────────────────────────────────────┐
│           Web 前端 (浏览器)           │
│   HTML + Tailwind CSS + Alpine.js    │
└──────────────┬──────────────────────┘
               │ HTTP API
┌──────────────▼──────────────────────┐
│         Flask 后端 (Python)          │
│                                      │
│  ┌──────────┐  ┌──────────────────┐ │
│  │ 爬虫模块  │  │ 文档生成模块      │ │
│  │scraper.py│  │doc_generator.py  │ │
│  └─────┬────┘  └───┬──────────┬───┘ │
│        │           │          │      │
│        │     ┌─────▼──┐ ┌────▼───┐  │
│        │     │python-  │ │Report- │  │
│        │     │docx     │ │Lab PDF │  │
│        │     │→ .docx  │ │→easyofd│  │
│        │     └────────┘ │→ .ofd  │  │
│        │                └────────┘  │
│  ┌─────▼──────────────────────────┐ │
│  │      APScheduler 定时任务       │ │
│  └────────────────────────────────┘ │
└──────────────────────────────────────┘
               │
     ┌─────────▼──────────┐
     │   output/ 目录结构   │
     │  年份/周报范围/领导/  │
     │  日期/(.docx + .ofd) │
     └────────────────────┘
```

## 二、技术栈

| 模块 | 技术 | 版本 | 用途 |
|------|------|------|------|
| Web框架 | Flask | 3.1.x | 后端API和页面路由 |
| 爬虫 | requests + BeautifulSoup4 + lxml | latest | 解析政府网站HTML |
| DOCX生成 | python-docx | 1.1.x | 按公文格式生成Word文档 |
| PDF生成 | ReportLab | 4.4.x | 生成带矢量文本的PDF（中间产物）|
| OFD转换 | easyofd | 0.5.x | PDF→OFD转换，保留可选中文本 |
| 定时任务 | APScheduler | 3.11.x | 每日定时采集 |
| 前端框架 | Tailwind CSS (CDN) + Alpine.js (CDN) | latest | 响应式UI + 交互 |

## 三、OFD 生成策略

**核心流程**: 文章正文 → ReportLab PDF（矢量文本） → easyofd pdf2ofd → OFD

### 关键实现

```python
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from easyofd.ofd import OFD

# 1. 用 ReportLab 生成公文排版 PDF
c = canvas.Canvas(buffer, pagesize=A4)
# ... 按公文格式排版 ...
c.save()

# 2. 用 easyofd 转换为 OFD
ofd = OFD()
ofd_bytes = ofd.pdf2ofd(pdf_bytes, optional_text=True)
ofd.del_data()
```

### 备选方案

若 easyofd 转换效果不理想，可直接构建 OFD XML 包：
- OFD 本质是 ZIP 文件，内含 XML（遵循 GB/T 33190-2016）
- 手动构造 OFD.xml、Document.xml、Page XML、字体资源等

## 四、字体方案

系统已安装：
- **方正小标宋简体** — 标题用
- **仿宋GB2312** — 正文用
- **Times New Roman** — 数字和英文

ReportLab 中注册中文字体：
```python
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

pdfmetrics.registerFont(TTFont('FZXiaoBiaoSong', 'C:/Windows/Fonts/FZXBSJW.TTF'))
pdfmetrics.registerFont(TTFont('FangSong_GB2312', 'C:/Windows/Fonts/simfang.ttf'))
```

## 五、目录结构规范

```
output/门户网站周报/
└── {年份}年/
    └── {起始日期}-{结束日期}/
        └── {领导姓名}{职务}/
            └── {活动月日}/
                ├── {活动标题简称}.docx
                └── {活动标题简称}.ofd
```

文件名处理：
- 最大长度 80 字符（避免路径过长）
- 移除不安全文件名字符：`\ / : * ? " < > |`

## 六、API 设计

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/` | 主页面 |
| GET | `/api/config` | 获取当前配置 |
| POST | `/api/config` | 更新配置 |
| GET | `/api/reports` | 获取已采集的周报列表 |
| POST | `/api/collect` | 手动触发采集（可指定某一期） |
| GET | `/api/reports/<id>/detail` | 获取某期周报解析详情 |
| POST | `/api/generate/<id>` | 手动触发生成某期文件 |
| GET | `/api/download/<path>` | 下载文件 |
| GET | `/api/status` | 获取采集/生成任务状态 |

## 七、前端设计

### 风格：红色公文风

| 元素 | 设计值 |
|------|--------|
| 主色 | `#C41E24`（中国红）|
| 辅色 | `#B8860B`（暗金/铜黄）|
| 背景色 | `#FAFAF5`（暖米白）|
| 文字色 | `#1A1A1A`（近黑）|
| 次要文字 | `#6B7280`（灰色）|
| 标题字体 | Noto Serif SC / 思源宋体 |
| 正文字体 | Noto Sans SC / 思源黑体 |
| 边框/分隔 | `#E5E2D9`（暖灰）|
| 卡片背景 | `#FFFFFF` |

### 页面布局

1. **顶部**: 红色横杠 + 标题（类公文红头）
2. **配置区**: URL输入框 + 定时开关 + 手动采集按钮
3. **周报列表**: 卡片式布局，每张显示日期范围、领导活动数、状态、下载
4. **详情面板**: 点击展开查看该期所有领导动态和文件下载

## 八、项目文件结构

```
gov-weekly-ofd/
├── app.py                  # Flask 主应用 + API路由
├── scraper.py              # 网页爬虫解析模块
├── doc_generator.py        # DOCX + OFD 文档生成
├── scheduler.py            # APScheduler 定时任务
├── config.json             # 用户配置
├── requirements.txt        # Python 依赖
├── docs/
│   ├── REQUIREMENTS.md     # 需求文档
│   └── DESIGN.md           # 本技术方案
├── output/                 # 生成文件存放目录
│   └── 门户网站周报/
├── static/
│   └── favicon.svg         # 图标
├── templates/
│   └── index.html          # 主页面
└── fonts/                  # 字体文件备份（可选）
```

## 九、开发计划

| 阶段 | 内容 | 依赖 |
|------|------|------|
| P1 | 项目骨架 + 前端页面 | 无 |
| P2 | 爬虫模块（栏目页 → 周报 → 活动 → 正文）| 无 |
| P3 | DOCX 生成（python-docx 公文排版）| P2 |
| P4 | OFD 生成（ReportLab PDF → easyofd OFD）| P2 |
| P5 | 目录结构管理 + 文件组织 | P3, P4 |
| P6 | 前后端串联（API + 前端交互）| P1, P5 |
| P7 | 定时自动采集 | P6 |
| P8 | 端到端测试 | 全部 |
| P9 | Agent Skill 封装 | P8 |
