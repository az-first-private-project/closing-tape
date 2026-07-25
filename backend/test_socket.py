"""最底层测试：Python socket 能否绑定端口"""
import socket
import sys

print("Python版本:", sys.version)
print()

# 测试1: 创建 socket
print("[测试1] 创建 socket...")
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
print("  ✅ socket 创建成功")

# 测试2: 绑定端口
print("[测试2] 绑定 127.0.0.1:9999...")
try:
    s.bind(("127.0.0.1", 9999))
    print("  ✅ 端口绑定成功")
except Exception as e:
    print(f"  ❌ 绑定失败: {e}")
    sys.exit(1)

# 测试3: 监听
print("[测试3] 开始监听...")
s.listen(1)
print("  ✅ 正在监听 127.0.0.1:9999")
print()
print("🎉 你的 Python socket 完全正常！")
print("   问题出在上层库（uvicorn/hypercorn/waitress）")
print()
print("服务器正在运行，用浏览器访问 http://127.0.0.1:9999")

# 等待一个连接
s.settimeout(10)
try:
    conn, addr = s.accept()
    print(f"\n收到来自 {addr} 的连接！")
    conn.send(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\nHello from raw Python socket!")
    conn.close()
except socket.timeout:
    print("\n(10秒内无连接，正常退出)")
s.close()