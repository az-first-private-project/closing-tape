"""
GFNA 启动工具（Windows 最终方案）
--------------------------------
使用 Waitress（纯同步 WSGI 服务器），完全绕开 asyncio。
Waitress 专为 Windows 设计，稳定性极高。

启动方式:
    cd backend
    python run.py
"""
import sys
import os

# 确保 backend 目录在 Python 路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 加载环境变量
from dotenv import load_dotenv
load_dotenv()

# 导入 FastAPI 应用
from app.main import app

# 把 ASGI 应用包装成 WSGI 应用（Waitress 只支持 WSGI）
from a2wsgi import ASGIMiddleware
wsgi_app = ASGIMiddleware(app)

if __name__ == "__main__":
    print("=" * 50)
    print("  GFNA - 全球财经新闻聚合器")
    print("=" * 50)
    print()
    print(f"  主页:     http://localhost:8000/")
    print(f"  API文档:  http://localhost:8000/docs")
    print(f"  健康检查: http://localhost:8000/health")
    print()
    print(f"  服务器:   Waitress (同步模式，无 asyncio)")
    print("  按 Ctrl+C 停止服务")
    print("=" * 50)
    print()

    from waitress import serve
    serve(wsgi_app, host="0.0.0.0", port=8000, threads=4)