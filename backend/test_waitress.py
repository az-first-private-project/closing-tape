"""测试：Waitress 能不能在你的 Windows 上启动"""
print("开始测试...")

# 一个最简单的 WSGI 应用（不需要 FastAPI，不需要 asyncio）
def simple_app(environ, start_response):
    status = "200 OK"
    headers = [("Content-Type", "text/plain; charset=utf-8")]
    start_response(status, headers)
    return [b"Hello World! Waitress is working!"]

print("导入 waitress...")
from waitress import serve
print("启动服务器...")
print("如果看到 Serving on http://0.0.0.0:9000 就说明成功了")
serve(simple_app, host="0.0.0.0", port=9000)