# 政务周报 OFD 生成器

![Python](https://img.shields.io/badge/Python-3.12+-blue)
![Flask](https://img.shields.io/badge/Flask-3.1+-green)
![License](https://img.shields.io/badge/License-MIT-orange)

一个支持从政府网站自动抓取周报、生成 DOCX 和 OFD 格式文档的 Flask 应用。

## 功能特性

- 🌐 **网页爬虫**：自动抓取湖北政府网站最新周报内容
- 📄 **文档生成**：同时生成 DOCX（Word）和 OFD（版式文件）格式
- 🎯 **格式标准**：完全符合公文格式规范（居中标题、行距、字体等）
- 🔤 **中英混排**：完美支持中文、英文、数字的混合排版
- ⏰ **定时任务**：支持定时自动爬取和生成报告
- 💼 **Web UI**：提供友好的网页界面供下载文档

## 快速开始

### 前置要求

- Python 3.12+
- Windows/Linux/macOS

### 安装

1. **克隆仓库**
```bash
git clone https://github.com/wuliaolll/gov-weekly-ofd.git
cd gov-weekly-ofd
```

2. **创建虚拟环境**
```bash
python -m venv .venv

# Windows
.venv\\Scripts\\activate

# Linux/macOS
source .venv/bin/activate
```

3. **安装依赖**
```bash
pip install -r requirements.txt
```

### 运行应用

```bash
# Windows
.venv\\Scripts\\python.exe app.py

# Linux/macOS
python app.py
```

访问 [http://localhost:5000](http://localhost:5000) 打开应用。

## 项目结构

```
gov-weekly-ofd/
├── app.py                 # Flask 主应用
├── config.json            # 配置文件
├── requirements.txt       # 依赖列表
├── scraper.py             # 网络爬虫模块
├── scheduler.py           # 定时任务模块
├── doc_generator.py       # DOCX/OFD 生成模块
├── templates/
│   └── index.html         # 前端页面
├── static/
│   └── favicon.svg        # 网站图标
├── docs/
│   ├── DESIGN.md          # 架构设计文档
│   └── REQUIREMENTS.md    # 功能需求文档
└── output/                # 生成文件输出目录（.gitignore）
```

## 配置

编辑 `config.json` 配置爬虫和任务参数：

```json
{
  "column_url": "https://www.hubei.gov.cn/hbfb/zwzb/index.shtml",
  "schedule_hour": 8,
  "schedule_minute": 0,
  "auto_collect": true
}
```

## 核心模块说明

### scraper.py
- 使用 curl.exe 绕过 SSL 验证
- BeautifulSoup 解析 HTML
- 返回格式化的段落列表

### doc_generator.py
- 使用 python-docx 生成 DOCX
- 使用 ReportLab + easyofd 生成 OFD
- 支持字体混合，完美处理中文标点排版

### app.py
- Flask 后端 API
- 提供文档下载接口

## 字体要求

应用会自动检测系统字体。为获得最佳效果，建议安装：

- **标题**：方正小标宋简体（FZXBSJW.TTF）
- **正文**：仿宋 GB2312 或标准仿宋体简（FangSong）
- **英文**：Times New Roman

字体搜索路径：
- Windows: `C:/Windows/Fonts` 和 `~/AppData/Local/Microsoft/Windows/Fonts`

## API 端点

### 获取最新周报
```
GET /api/latest
```

响应示例：
```json
{
  "title": "湖北省政府周报第10期",
  "content": "...",
  "pub_date": "2026-04-13",
  "paragraphs": [...]
}
```

### 生成文档
```
POST /api/generate
Content-Type: application/json

{
  "title": "文档标题",
  "content": "文档内容",
  "format": "docx" | "ofd" | "both"
}
```

### 下载文件
```
GET /download/<filename>
```

## 故障排除

### PDF/OFD 显示乱码
- 检查系统是否安装了中文字体
- 验证字体路径是否正确

### 爬虫超时
- 检查网络连接
- 调整 `config.json` 中的超时参数

### SSL 错误
- 在 Windows 上自动使用 curl.exe 处理
- Linux/macOS 需安装 libcurl

## 文档

查看 `docs/` 目录了解详细的设计文档和需求文档。

## 许可证

MIT License - 详见 LICENSE 文件

## 贡献

欢迎提交 Issue 和 Pull Request！

---

**最后更新**：2026-04-13