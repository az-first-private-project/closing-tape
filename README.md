# 📊 GFNA — 全球财经新闻聚合器

> **Global Finance News Aggregator**
>
> 一个基于 Python 的全栈 Web 应用，自动爬取全球主流财经新闻，并利用 **DeepSeek AI** 生成中文摘要。

> 🔨 **V2 重构进行中**: 详见 [ROADMAP-V2.md](docs/ROADMAP-V2.md)

---

## 🎯 项目简介

本项目是《数据库原理课程设计》的实践作品，V2 升级为全栈作品，融合了：
- **爬虫技术** (Scrapy + Playwright)
- **数据库设计** (MySQL + SQLAlchemy ORM)
- **AI 自然语言处理** (DeepSeek API)
- **Web 前后端开发** (FastAPI + HTML/CSS/JS)

### 核心功能

| 功能 | 描述 |
|------|------|
| 🔄 定时爬取 | 每小时自动抓取 Reuters、Bloomberg、CNBC、MarketWatch 的财经新闻 |
| 🎯 智能去重 | Bloom Filter + URL 哈希双重去重机制 |
| 🤖 AI 摘要 | 调用 **DeepSeek API** 将英文长文总结为 200 字中文摘要 |
| 🌐 Web 展示 | 响应式网页，支持新闻列表浏览、详情查看、关键词搜索 |
| 📊 数据统计 | 实时显示文章总数、今日新增、已生成摘要数量 |

---

## 🛠️ 技术栈

| 层级 | 技术 |
|------|------|
| **后端框架** | Python 3.11 + FastAPI |
| **数据库** | MySQL 8.0 (InnoDB, utf8mb4) |
| **ORM** | SQLAlchemy 2.0 |
| **爬虫** | Scrapy + Playwright (JS 渲染) |
| **AI 模型** | DeepSeek API (`deepseek-chat`) |
| **前端** | HTML5 + CSS3 + JavaScript (原生) |
| **任务调度** | APScheduler |
| **部署** | Uvicorn + start.sh 一键启动 |

---

## 🚀 快速开始

### 1. 环境要求

- **Python** ≥ 3.9
- **MySQL** ≥ 8.0
- **Git Bash** (Windows) 或终端 (Linux/macOS)

### 2. 克隆项目

```bash
cd /data/workspace/gfna-project/
```

### 3. 配置环境变量

```bash
# 复制配置模板
cp backend/.env.example backend/.env

# 编辑 .env 文件，填入你的配置
# 重点修改以下两项:
#   DEEPSEEK_API_KEY=sk-xxxxxxxx    ← 你的 DeepSeek API Key
#   MYSQL_PASSWORD=your_password    ← 你的 MySQL 密码
```

### 4. 配置 DeepSeek API Key

> **重要**: 本项目使用 DeepSeek API 而非 Claude API

1. 访问 [DeepSeek 开放平台](https://platform.deepseek.com/)
2. 注册/登录账号
3. 在 `API Keys` 页面创建新的 API Key
4. 将 Key 填入 `backend/.env` 文件的 `DEEPSEEK_API_KEY` 字段

```env
DEEPSEEK_API_KEY=sk-your-api-key-here
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
```

### 5. 初始化数据库

```bash
# 方式1: 使用 mysql 命令行
mysql -u root -p < sql/init.sql

# 方式2: 登录 MySQL 后执行
mysql> source sql/init.sql;
```

### 6. 一键启动

```bash
chmod +x start.sh
./start.sh
```

### 7. 手动启动 (如果脚本不工作)

```bash
# 创建虚拟环境
cd backend
python -m venv venv

# 激活虚拟环境
source venv/bin/activate        # Linux/macOS
venv\Scripts\activate           # Windows

# 安装依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器
python -m playwright install chromium

# 启动服务
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

## 📖 API 接口文档

启动服务后访问:
- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)

### 主要接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | 主页 (HTML) |
| `GET` | `/health` | 健康检查 |
| `GET` | `/api/v1/articles` | 分页获取新闻列表 |
| `GET` | `/api/v1/articles/{id}` | 获取文章详情 |
| `GET` | `/api/v1/search?q=关键词` | 搜索新闻 |
| `GET` | `/api/v1/stats` | 系统统计 |
| `POST` | `/api/v1/crawl/trigger` | 手动触发爬虫 (需 API Key) |
| `POST` | `/api/v1/crawl/process-summaries` | 批量生成摘要 (需 API Key) |

---

## 📁 项目结构

```
gfna-project/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口
│   │   ├── api/v1/
│   │   │   └── articles.py      # 文章 API 路由
│   │   ├── core/
│   │   │   ├── config.py        # 配置管理
│   │   │   └── security.py      # API Key 验证
│   │   ├── db/
│   │   │   ├── base.py          # SQLAlchemy Base
│   │   │   ├── session.py       # 数据库会话
│   │   │   └── models.py        # ORM 模型
│   │   ├── schemas/
│   │   │   └── article.py       # Pydantic 校验
│   │   └── services/
│   │       ├── llm_service.py   # ⭐ DeepSeek AI 服务
│   │       └── crawler_service.py # 爬虫调度
│   ├── scraper/
│   │   └── finance_spider/
│   │       ├── spiders/
│   │       │   └── global_news.py # 爬虫核心逻辑
│   │       ├── items.py         # 数据项定义
│   │       ├── middlewares.py   # 中间件 (UA轮换)
│   │       ├── pipelines.py     # 管道 (去重+入库)
│   │       └── settings.py      # Scrapy 设置
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── index.html               # 主页
│   ├── detail.html              # 详情页
│   ├── css/style.css            # 样式表
│   └── js/main.js               # 前端逻辑
├── sql/
│   └── init.sql                 # 数据库建表脚本
├── docs/
│   └── architecture.md          # 架构设计文档
├── tests/
│   └── test_api.py              # API 测试
├── start.sh                     # 一键启动脚本
└── README.md                    # 本文件
```

---

## 🗄️ 数据库设计

### ER 概念模型

```
┌─────────────┐       ┌──────────────────┐
│ news_sources │ 1───N │  news_articles    │
├─────────────┤       ├──────────────────┤
│ id (PK)     │       │ id (PK)           │
│ name        │       │ source_id (FK)    │
│ base_url    │       │ title             │
│ country     │       │ url               │
│ language    │       │ url_hash (UNIQUE) │
│ is_active   │       │ content           │
│ crawl_interval│     │ summary ⭐        │
│ created_at  │       │ author            │
│ updated_at  │       │ publish_time      │
└─────────────┘       │ crawl_time        │
                      │ sentiment_score   │
                      │ is_processed      │
                      └──────────────────┘
```

### 去重机制

```
新URL → Bloom Filter (快速过滤)
         ↓ 可能存在
       MySQL UNIQUE 索引 (精确去重)
         ↓ 确认不重复
       写入数据库
```

---

## ⚠️ 常见问题

### 1. DeepSeek API Key 未配置
> 摘要显示"API Key 未配置"

在 `backend/.env` 中设置正确的 `DEEPSEEK_API_KEY`。

### 2. MySQL 连接失败
> sqlalchemy.exc.OperationalError

检查 MySQL 服务是否运行，以及 `.env` 中的数据库配置是否正确。

### 3. Playwright 浏览器未安装
> playwright._impl._api_types.Error

```bash
python -m playwright install chromium
```

### 4. 爬虫没有抓取到数据
> 新闻列表为空

- 检查网络连接 (部分外网可能需要代理)
- 目标网站可能更新了 HTML 结构，CSS 选择器需要调整
- 查看 `backend/scraper/` 下的运行日志

---

## 📝 协议与声明

- 本项目仅用于**课程设计教学**目的
- 新闻内容版权归原作者所有
- AI 摘要仅供学习参考，不构成投资建议
- 请遵守各新闻网站的 `robots.txt` 和使用条款

---

## 👥 适用场景

- 数据库原理课程设计
- 物联网工程综合实践
- Python 全栈开发学习
- 爬虫 + AI 实战项目

---

> **Built with ❤️ | GFNA Team | 2025**