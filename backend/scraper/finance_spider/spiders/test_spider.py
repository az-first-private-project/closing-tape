"""极简测试爬虫：只抓一个页面，确认 Scrapy 能正常工作"""
import scrapy

class TestSpider(scrapy.Spider):
    name = "test_spider"

    def start_requests(self):
        print("=" * 50)
        print("🔍 测试爬虫开始...")
        print("=" * 50)
        yield scrapy.Request(
            url="https://httpbin.org/get",
            callback=self.parse,
            errback=self.errback,
        )

    def parse(self, response):
        print(f"✅ 请求成功! 状态码: {response.status}")
        print(f"   URL: {response.url}")
        print(f"   内容前100字: {response.text[:100]}")
        yield {"status": "success", "url": response.url}

    def errback(self, failure):
        print(f"❌ 请求失败: {failure.value}")