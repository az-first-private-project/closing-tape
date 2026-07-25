"""
手动爬取脚本
-----------
直接运行爬虫 + 自动生成 AI 摘要。
在另一个终端窗口执行（不要关掉服务器）:

    cd backend
    python crawl_now.py
"""
import sys
import os
import subprocess

# 配置
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()

if __name__ == "__main__":
    print("=" * 50)
    print("  GFNA 手动爬取 + AI 摘要")
    print("=" * 50)
    print()

    # ===== 第1步：运行 Scrapy 爬虫 =====
    print("[第1步] 运行爬虫，抓取新闻...")
    print("-" * 40)

    scrapy_dir = os.path.join(os.path.dirname(__file__), "scraper")
    cmd = [sys.executable, "-m", "scrapy", "crawl", "global_news"]

    # 跳过 Playwright（如果没有安装的话）
    env = os.environ.copy()
    env.setdefault("SCRAPY_SETTINGS_MODULE", "finance_spider.settings")

    result = subprocess.run(
        cmd,
        cwd=scrapy_dir,
        capture_output=False,  # 实时显示输出
        text=True,
        timeout=300,  # 5分钟超时
        env=env,
    )
    print()

    if result.returncode != 0:
        print(f"[警告] 爬虫退出码: {result.returncode}")
        print("       可能是目标网站改版或网络问题，不影响已抓取的数据")
    else:
        print("[完成] 爬虫运行结束")

    # ===== 第2步：生成 AI 摘要 =====
    print()
    print("[第2步] 为新文章生成 AI 摘要...")
    print("-" * 40)

    from app.db.session import SessionLocal
    from app.db.models import NewsArticle
    from app.services.llm_service import generate_summary

    db = SessionLocal()
    try:
        # 查询未处理的文章
        articles = (
            db.query(NewsArticle)
            .filter(NewsArticle.is_processed == 0)
            .filter(NewsArticle.content.isnot(None))
            .filter(NewsArticle.content != "")
            .limit(10)
            .all()
        )

        if not articles:
            print("没有需要生成摘要的文章（可能爬虫未抓取到新内容）")
        else:
            print(f"找到 {len(articles)} 篇待处理文章")
            success = 0
            for i, art in enumerate(articles):
                print(f"  [{i+1}/{len(articles)}] 处理: {art.title[:50]}...")
                summary = generate_summary(art.content)
                if summary:
                    art.summary = summary
                    art.is_processed = 1
                    success += 1
                    print(f"    ✅ 摘要: {summary[:60]}...")
                else:
                    art.is_processed = 2
                    print(f"    ❌ 摘要生成失败")

            db.commit()
            print(f"\n摘要生成完成: {success}/{len(articles)} 篇成功")

    finally:
        db.close()

    print()
    print("=" * 50)
    print("  全部完成！刷新浏览器查看结果")
    print("=" * 50)