"""测试：最小化 FastAPI，排除 GFNA 代码干扰"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def root():
    return {"message": "Hello, FastAPI is working!"}

if __name__ == "__main__":
    print("Starting minimal server...")
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=9000, log_level="info", loop="asyncio")