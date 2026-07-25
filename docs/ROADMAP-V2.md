# 🚀 GFNA V2 重构计划

> **目标**：把一个"能跑"的课程设计，升级成"炫酷高端"的全栈作品
>
> **策略**：路线 B — 前端现代化 + PostgreSQL 替换 MySQL
>
> **原则**：分阶段推进，每阶段有可见成果，避免再次烂尾

---

## 📋 一、技术选型变更

| 层级 | V1（当前） | V2（目标） | 变更理由 |
|------|-----------|-----------|---------|
| **前端框架** | 原生 HTML/CSS/JS | React 18 + Vite + TypeScript | 组件化、现代化、市场主流 |
| **CSS 方案** | 手写 CSS | Tailwind CSS + shadcn/ui | 颜值即正义，shadcn 组件库开箱即用 |
| **状态管理** | 全局 state 对象 | TanStack Query (React Query) | 服务端状态缓存、自动重刷、分页内置 |
| **图表** | 无 | Recharts | 仪表盘数据可视化 |
| **动画** | 无 | Framer Motion | 页面过渡、卡片动效 |
| **数据库** | MySQL 8.0 | PostgreSQL 16 | 全文搜索 (`tsvector`)、JSONB、更强的 SQL 标准支持 |
| **数据库驱动** | PyMySQL | asyncpg (异步) | FastAPI 异步优势最大化 |
| **后端框架** | FastAPI（不变） | FastAPI（不变） | 已经很好，只需适配 asyncpg |
| **爬虫** | Scrapy（不变） | Scrapy（不变） | 够用，CSS 选择器抽配置 |
| **AI** | DeepSeek（不变） | DeepSeek（不变） | 中文效果好，性价比高 |

---

## 🗺️ 二、分阶段实施计划

### 🔴 Phase 0：地基（第1天，务必完成）

> **目标**：项目不再"裸奔"，先装好安全网

- [x] 项目目录已存在
- [ ] **git init + 首次提交**（坟场脚本已经警告过了——没 git 的项目就是没墓碑的尸体）
- [ ] 创建 `.gitignore`（排除 node_modules、.venv、__pycache__、.env）
- [ ] 创建 GitHub 仓库并 push
- [ ] 在 `README.md` 中更新 V2 计划链接

**完成标志**：`git log` 有提交记录，GitHub 上能看到仓库

---

### 🟠 Phase 1：后端改造（第1-3天）

> **目标**：MySQL → PostgreSQL，后端先能跑起来

#### 1.1 数据库切换

```
MySQL                          →  PostgreSQL
─────────────────────────────────────────────────────
VARCHAR / TEXT                 →  VARCHAR / TEXT（不变）
UNIQUE INDEX (url_hash)        →  UNIQUE INDEX（不变）
SQL LIKE '%keyword%'           →  tsvector + GIN 索引（全文本搜索！）
DATETIME                       →  TIMESTAMP WITH TIME ZONE
INT AUTO_INCREMENT             →  SERIAL / BIGSERIAL
```

**具体任务**：
- [ ] 安装 PostgreSQL 16（或用 Docker：`docker run -d --name gfna-pg -p 5432:5432 -e POSTGRES_PASSWORD=... postgres:16`）
- [ ] 重写 `sql/init.sql` → PostgreSQL DDL 语法
- [ ] 修改 `backend/app/db/session.py`：同步 Session → 异步 `AsyncSession`
- [ ] 修改 `backend/app/db/models.py`：适配 PostgreSQL 类型
- [ ] 修改 `backend/app/core/config.py`：`DATABASE_URL` 改为 asyncpg 格式
- [ ] 修改 `backend/app/api/v1/articles.py`：所有 `db.query()` → async SQLAlchemy 2.0 写法
- [ ] 升级搜索接口：`LIKE` → PostgreSQL `to_tsvector('chinese', content) @@ plainto_tsquery('chinese', keyword)`
- [ ] 安装 `pip install asyncpg sqlalchemy[asyncio]`

#### 1.2 爬虫部分保持不变
- Scrapy 是同步框架，单独运行，不接入异步改造
- 只修改 `pipelines.py` 的连接字符串适配 PostgreSQL

**完成标志**：`uvicorn app.main:app` 启动成功，`/docs` 页面能访问，数据库表自动创建

---

### 🟡 Phase 2：前端重生（第3-7天，核心阶段）

> **目标**：原生 HTML → React + Tailwind + shadcn/ui，颜值起飞

#### 2.1 项目初始化
```bash
npm create vite@latest frontend-v2 -- --template react-ts
cd frontend-v2
npm install
npm install tailwindcss @tailwindcss/vite        # Tailwind v4
npm install @tanstack/react-query                 # 服务端状态
npm install recharts                              # 图表
npm install framer-motion                         # 动画
npm install react-router-dom                      # 路由
npm install lucide-react                          # 图标（shadcn/ui 依赖）
```

#### 2.2 页面设计

```
┌──────────────────────────────────────────────────┐
│  📊 GFNA                          🔍 搜索  🌙    │  ← 顶部导航（毛玻璃效果）
├──────────────────────────────────────────────────┤
│  📰 1,234 篇     ✅ 856 篇摘要     🔄 正在监听    │  ← 统计卡片行（数字跳动动画）
├──────────────────────────────────────────────────┤
│  [全部] [Reuters] [Bloomberg] [CNBC] [MarketWatch]│  ← 筛选标签（滑动指示器）
├──────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌───────────┐ │
│  │ Bloomberg   │  │  Reuters    │  │  CNBC     │ │  ← 新闻卡片网格
│  │ 美联储维持   │  │ 标普500...  │  │ 科技股... │ │     (hover 3D 悬浮)
│  │ 利率不变...  │  │             │  │           │ │
│  │ 🤖 AI已总结 │  │ ⏳ 待处理   │  │ 🤖 AI已总结│ │
│  └─────────────┘  └─────────────┘  └───────────┘ │
├──────────────────────────────────────────────────┤
│              ← 上一页  1/12  下一页 →             │  ← 分页
└──────────────────────────────────────────────────┘
```

#### 2.3 组件树

```
App
├── Layout
│   ├── Navbar (毛玻璃, 深色/浅色切换)
│   │   ├── Logo
│   │   ├── SearchBar (带自动补全)
│   │   └── ThemeToggle
│   ├── StatsBar (三个统计卡片，数字滚动动画)
│   ├── FilterTabs (来源筛选，带动画指示器)
│   └── Footer
├── Pages
│   ├── HomePage
│   │   ├── ArticleGrid
│   │   │   └── ArticleCard (hover 3D, 来源色标, AI 状态徽章)
│   │   └── Pagination
│   ├── DetailPage (新闻详情 + AI 摘要 + 原文链接)
│   ├── DashboardPage (数据可视化)
│   │   ├── SourcePieChart (来源分布饼图)
│   │   ├── DailyTrendChart (每日新增趋势)
│   │   └── ProcessingStatusChart (AI 处理进度)
│   └── AboutPage
└── Modal
    └── ArticleModal (快速预览，Command+K 唤起)
```

#### 2.4 前端视觉亮点

| 效果 | 技术 | 描述 |
|------|------|------|
| **毛玻璃导航栏** | `backdrop-blur` + Tailwind | 滚动时导航栏半透明模糊 |
| **数字跳动** | Framer Motion `useSpring` | 统计数字从 0 弹跳到实际值 |
| **卡片 3D 悬浮** | CSS `transform: rotateX/Y` + Framer Motion | 鼠标悬停卡片有倾斜光影效果 |
| **骨架屏** | Tailwind `animate-pulse` | 加载中显示灰色骨架占位 |
| **深色模式** | Tailwind `dark:` + CSS 变量 | 一键切换深色/浅色 |
| **快捷键** | `Cmd+K` 搜索面板 | 类似 Linear/Notion 的命令面板 |
| **滚动渐显** | Framer Motion `whileInView` | 卡片滚入视口时从下淡入 |

**完成标志**：`npm run dev` 启动，浏览器看到新前端，能调通后端 API 拿到数据

---

### 🟢 Phase 3：数据可视化仪表盘（第7-8天）

> **目标**：不只是看新闻，还要看数据

新增 `/dashboard` 页面：

```
┌──────────────────────────────────────────────┐
│  📊 数据仪表盘                                │
├──────────────────┬───────────────────────────┤
│  新闻来源分布     │  每日新增趋势              │
│  (饼图)          │  (面积图)                  │
│  Reuters  35%   │  ▂▃▅▇█▆▄▂▁               │
│  Bloomberg 28%  │  Jun7  Jun10  Jun13       │
│  CNBC     22%  │                            │
│  MktWatch 15%   │                            │
├──────────────────┼───────────────────────────┤
│  AI处理进度       │  热门关键词（词云）         │
│  (环形进度条)     │  inflation fed rate       │
│  ████████░░ 82% │  stock market tech...      │
└──────────────────┴───────────────────────────┘
```

**后端新增 API**：
- [ ] `GET /api/v1/stats/daily-trend?days=30` — 每日新增文章数
- [ ] `GET /api/v1/stats/keywords` — 热门关键词（从标题提取）
- [ ] `GET /api/v1/stats/source-distribution` — 来源分布

---

### 🔵 Phase 4：体验打磨（第8-10天）

- [ ] **PWA 支持**：加 `manifest.json` + Service Worker，手机可以"安装"到桌面
- [ ] **推送通知**：浏览器通知"今日财经要闻已更新"
- [ ] **无限滚动**：替代分页按钮，滚到底部自动加载更多
- [ ] **AI 语音播报**：点一下按钮，朗读 AI 摘要（Web Speech API）
- [ ] **导出功能**：选中新闻导出为 PDF/Markdown
- [ ] **加载动画**：页面切换的过渡动画（Framer Motion `AnimatePresence`）

---

### 🟣 Phase 5：部署上线（第10-12天，可选）

- [ ] Docker Compose 编排（FastAPI + PostgreSQL + React）
- [ ] 部署到 Vercel（前端）+ Railway（后端 + 数据库）
- [ ] 配置自定义域名
- [ ] CI/CD（GitHub Actions：push → test → deploy）

---

## 🏗️ 三、新项目目录结构

```
gfna-project/
├── backend/                    # 后端（FastAPI + PostgreSQL）
│   ├── app/
│   │   ├── main.py             # FastAPI 入口
│   │   ├── api/v1/
│   │   │   ├── articles.py     # 新闻 CRUD + 搜索
│   │   │   ├── stats.py        # 统计仪表盘 API [新增]
│   │   │   └── crawl.py        # 爬虫触发 [从 articles.py 拆出]
│   │   ├── core/
│   │   │   ├── config.py       # 统一配置
│   │   │   └── security.py
│   │   ├── db/
│   │   │   ├── base.py
│   │   │   ├── session.py      # AsyncSession
│   │   │   └── models.py       # ORM 模型
│   │   ├── schemas/
│   │   │   └── article.py      # Pydantic
│   │   └── services/
│   │       ├── llm_service.py   # DeepSeek（不变）
│   │       └── crawler_service.py
│   ├── scraper/                 # 爬虫（Scrapy，同步运行）
│   │   └── finance_spider/
│   ├── requirements.txt
│   └── .env.example
├── frontend-v2/                 # 前端（React + Vite + TS）[新增]
│   ├── src/
│   │   ├── components/
│   │   │   ├── ui/              # shadcn/ui 组件
│   │   │   ├── Navbar.tsx
│   │   │   ├── StatsBar.tsx
│   │   │   ├── ArticleCard.tsx
│   │   │   └── ...
│   │   ├── pages/
│   │   │   ├── HomePage.tsx
│   │   │   ├── DetailPage.tsx
│   │   │   └── DashboardPage.tsx
│   │   ├── hooks/
│   │   │   └── useArticles.ts   # TanStack Query hooks
│   │   ├── lib/
│   │   │   └── api.ts           # Axios/fetch 封装
│   │   ├── App.tsx
│   │   └── main.tsx
│   ├── index.html
│   ├── tailwind.config.ts
│   ├── vite.config.ts
│   └── package.json
├── sql/
│   └── init.sql                 # PostgreSQL DDL [重写]
├── docs/
│   ├── architecture.md
│   └── ROADMAP-V2.md           # 本文件
├── docker-compose.yml           # [新增] 一键启动全部服务
├── README.md
└── .gitignore                   # [新增]
```

---

## ⚠️ 四、风险与应对

| 风险 | 概率 | 应对 |
|------|------|------|
| **PostgreSQL 装不上** | 中 | 用 Docker，一条命令搞定，不折腾本地安装 |
| **React + TypeScript 学习曲线** | 高 | 你是大一新生，React 要学的不少——先用 shadcn/ui 的现成组件搭积木，别自己造轮子 |
| **前后端联调跨域** | 中 | Vite proxy 配置，开发环境一键转发到 FastAPI |
| **爬虫 CSS 选择器失效** | 高 | 新闻网站经常改版，优先爬 RSS/API，CSS 当做 fallback |
| **DeepSeek API 额度用完** | 低 | 摘要功能是异步的，没额度了页面还能正常用，只是"AI 摘要待生成" |
| **再次烂尾** | 高 | **每个 Phase 控制在 2-3 天内完成**，每完成一个 Phase 就 git commit，有成就感才有动力继续 |

---

## 🎯 五、Phase 0 立即行动清单

把这篇文档看完后，现在就做这三件事：

```bash
# 1. 初始化 git（30秒）
cd "C:/Users/21468/OneDrive/Desktop/claude code 全栈项目/gfna-project"
git init
git add -A
git commit -m "init: GFNA V1 课程设计原始版本"

# 2. 创建 .gitignore
echo "node_modules/
.venv/
__pycache__/
.env
*.pyc
.DS_Store
dist/
.vite/" > .gitignore

git add .gitignore
git commit -m "chore: 添加 .gitignore"

# 3. 提交本计划文档
git add docs/ROADMAP-V2.md
git commit -m "docs: V2 重构计划 — 前端现代化 + PostgreSQL"
```

---

> **最后提醒**：这个计划看起来很宏大，但别被吓到。Phase 0+1 只需要把数据库换了、让旧前端先跑起来；Phase 2 才是重头戏。每个 Phase 做完就有东西能看，别一口气想着全部做完——那是上一次死因的重演。
>
> 好了，开始干活吧 🔨
