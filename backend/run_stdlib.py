"""
GFNA 启动工具（纯标准库方案）
----------------------------
使用 Python 内置 http.server + 多线程，零第三方依赖。
适用于任何第三方服务器库都挂了的极端情况。

启动方式:
    cd backend
    python run_stdlib.py

原理:
    - 用 http.server 提供静态文件（HTML/CSS/JS）
    - 用多线程处理请求（不阻塞）
    - 前端通过 JavaScript fetch() 调用 API
    - API 路由手动分发到对应处理函数
"""
import sys
import os
import json
import threading
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# 配置
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

# 导入应用模块（在启动时先验证数据库等是否正常）
from app.main import app
print("[预检] ✅ FastAPI 应用加载成功")

# ==================== API 处理函数 ====================

def handle_api(path, query_params, db_session):
    """简易 API 路由分发"""
    from app.db.session import SessionLocal
    from app.db.models import NewsArticle, NewsSource
    from sqlalchemy import func, or_
    from datetime import date

    db = SessionLocal()
    try:
        if path == "/api/v1/articles":
            page = int(query_params.get("page", [1])[0])
            page_size = int(query_params.get("page_size", [20])[0])
            source_id = query_params.get("source_id", [None])[0]

            query = (
                db.query(NewsArticle, NewsSource.name.label("source_name"))
                .join(NewsSource, NewsArticle.source_id == NewsSource.id)
                .filter(NewsArticle.is_processed != 2)
            )
            if source_id:
                query = query.filter(NewsArticle.source_id == int(source_id))

            total = query.count()
            total_pages = max(1, (total + page_size - 1) // page_size)
            offset = (page - 1) * page_size

            results = (
                query.order_by(NewsArticle.publish_time.desc())
                .offset(offset).limit(page_size).all()
            )

            items = []
            for art, src_name in results:
                items.append({
                    "id": art.id,
                    "title": art.title,
                    "url": art.url,
                    "content": art.content[:200] if art.content else None,
                    "author": art.author,
                    "publish_time": art.publish_time.isoformat() if art.publish_time else None,
                    "source_id": art.source_id,
                    "source_name": src_name,
                    "summary": art.summary,
                    "url_hash": art.url_hash,
                    "crawl_time": art.crawl_time.isoformat() if art.crawl_time else None,
                    "is_processed": art.is_processed,
                    "sentiment_score": art.sentiment_score,
                })

            return 200, {
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "items": items,
            }

        elif path.startswith("/api/v1/articles/") and len(path.split("/")) == 5:
            article_id = int(path.split("/")[-1])
            result = (
                db.query(NewsArticle, NewsSource.name.label("source_name"))
                .join(NewsSource, NewsArticle.source_id == NewsSource.id)
                .filter(NewsArticle.id == article_id).first()
            )
            if not result:
                return 404, {"detail": f"文章 ID={article_id} 不存在"}

            art, src_name = result
            return 200, {
                "id": art.id, "title": art.title, "url": art.url,
                "content": art.content, "author": art.author,
                "publish_time": art.publish_time.isoformat() if art.publish_time else None,
                "source_id": art.source_id, "source_name": src_name,
                "summary": art.summary, "url_hash": art.url_hash,
                "crawl_time": art.crawl_time.isoformat() if art.crawl_time else None,
                "is_processed": art.is_processed,
                "sentiment_score": art.sentiment_score,
            }

        elif path == "/api/v1/search":
            q = query_params.get("q", [""])[0]
            page = int(query_params.get("page", [1])[0])
            page_size = int(query_params.get("page_size", [20])[0])
            search_term = f"%{q}%"

            filters = or_(
                NewsArticle.title.like(search_term),
                NewsArticle.content.like(search_term),
                NewsArticle.summary.like(search_term),
            )
            query = (
                db.query(NewsArticle, NewsSource.name.label("source_name"))
                .join(NewsSource, NewsArticle.source_id == NewsSource.id)
                .filter(filters)
            )
            total = query.count()
            total_pages = max(1, (total + page_size - 1) // page_size)
            offset = (page - 1) * page_size
            results = query.order_by(NewsArticle.publish_time.desc()).offset(offset).limit(page_size).all()

            items = []
            for art, src_name in results:
                items.append({
                    "id": art.id, "title": art.title, "url": art.url,
                    "content": art.content[:200] if art.content else None,
                    "author": art.author,
                    "publish_time": art.publish_time.isoformat() if art.publish_time else None,
                    "source_id": art.source_id, "source_name": src_name,
                    "summary": art.summary, "url_hash": art.url_hash,
                    "crawl_time": art.crawl_time.isoformat() if art.crawl_time else None,
                    "is_processed": art.is_processed,
                    "sentiment_score": art.sentiment_score,
                })

            return 200, {
                "total": total, "page": page, "page_size": page_size,
                "total_pages": total_pages, "items": items,
            }

        elif path == "/api/v1/stats":
            from datetime import date
            total_articles = db.query(func.count(NewsArticle.id)).scalar() or 0
            total_sources = db.query(func.count(NewsSource.id)).scalar() or 0
            articles_today = (
                db.query(func.count(NewsArticle.id))
                .filter(func.date(NewsArticle.crawl_time) == date.today()).scalar() or 0
            )
            processed = (
                db.query(func.count(NewsArticle.id))
                .filter(NewsArticle.is_processed == 1).scalar() or 0
            )
            return 200, {
                "total_articles": total_articles, "total_sources": total_sources,
                "articles_today": articles_today, "processed_count": processed,
            }

        elif path == "/health":
            return 200, {"status": "ok", "service": "GFNA", "version": "1.0.0"}

        else:
            return 404, {"detail": "Not Found"}

    except Exception as e:
        return 500, {"detail": str(e)}
    finally:
        db.close()


# ==================== HTTP 请求处理器 ====================

# 静态文件根目录
FRONTEND_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "frontend"
)

class GFNAHandler(SimpleHTTPRequestHandler):
    """统一的请求处理器：静态文件 + API 路由"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=FRONTEND_DIR, **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query_params = parse_qs(parsed.query)

        # 如果是 API 请求
        if path.startswith("/api/") or path == "/health":
            status_code, data = handle_api(path, query_params, None)
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

        # 如果是根路径 → 返回 index.html
        elif path == "/":
            self.path = "/index.html"
            super().do_GET()

        # 静态文件：去掉 /static/ 前缀（HTML里引用的是 /static/css/xxx, 但实际文件在 /css/xxx）
        elif path.startswith("/static/"):
            self.path = path[7:]  # 去掉 "/static"
            super().do_GET()

        # 否则 → 静态文件
        else:
            super().do_GET()

    def log_message(self, format, *args):
        """简化日志输出"""
        print(f"  [{self.command}] {args[0]}")

    def end_headers(self):
        # 给所有响应加上 CORS 头
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()


# ==================== 多线程 HTTP 服务器 ====================

class ThreadedHTTPServer(HTTPServer):
    """每个请求开一个新线程处理（不阻塞）"""
    def process_request(self, request, client_address):
        t = threading.Thread(target=self.finish_request,
                            args=(request, client_address))
        t.daemon = True
        t.start()


if __name__ == "__main__":
    PORT = 8000
    server = ThreadedHTTPServer(("0.0.0.0", PORT), GFNAHandler)

    print("=" * 50)
    print("  GFNA - 全球财经新闻聚合器")
    print("=" * 50)
    print()
    print(f"  主页:     http://localhost:{PORT}/")
    print(f"  API:      http://localhost:{PORT}/api/v1/articles")
    print(f"  健康检查: http://localhost:{PORT}/health")
    print()
    print(f"  服务器:   Python 标准库 http.server (多线程)")
    print("  按 Ctrl+C 停止服务")
    print("=" * 50)
    print()
    print(f"[INFO] 服务已启动，监听端口 {PORT}...")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] 服务已停止")
        server.shutdown()