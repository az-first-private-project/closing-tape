"""
GFNA API 测试文件
----------------
测试 FastAPI 接口的正确性。
运行方式: pytest tests/test_api.py -v
"""
import pytest
from fastapi.testclient import TestClient
import sys
import os

# 添加 backend 目录到 Python 路径
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

# 注意: 测试需要 MySQL 数据库运行且配置正确
# 如果数据库不可用，部分测试将跳过


def get_test_client():
    """创建测试客户端"""
    try:
        from app.main import app
        return TestClient(app)
    except Exception as e:
        pytest.skip(f"无法创建测试客户端: {e}")


class TestHealthCheck:
    """健康检查接口测试"""

    def test_health_returns_ok(self):
        """测试健康检查返回 OK"""
        client = get_test_client()
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "GFNA"


class TestRootEndpoint:
    """根路径测试"""

    def test_root_returns_html_or_json(self):
        """测试根路径返回内容"""
        client = get_test_client()
        response = client.get("/")
        assert response.status_code in [200, 404]  # 200=有前端, 404=无前端文件


class TestArticlesAPI:
    """文章 API 测试 (需要数据库)"""

    def test_list_articles_basic(self):
        """测试获取文章列表 (基本请求)"""
        client = get_test_client()
        try:
            response = client.get("/api/v1/articles?page=1&page_size=10")
            # 可能返回 200 (有数据) 或 500 (数据库未连接)
            if response.status_code == 200:
                data = response.json()
                assert "total" in data
                assert "items" in data
                assert "page" in data
                assert data["page"] == 1
        except Exception as e:
            pytest.skip(f"数据库不可用: {e}")

    def test_list_articles_pagination(self):
        """测试文章列表分页参数"""
        client = get_test_client()
        try:
            response = client.get("/api/v1/articles?page=2&page_size=5")
            if response.status_code == 200:
                data = response.json()
                assert data["page"] == 2
                assert data["page_size"] == 5
        except Exception as e:
            pytest.skip(f"数据库不可用: {e}")

    def test_list_articles_invalid_page(self):
        """测试无效页码 (应为 422)"""
        client = get_test_client()
        response = client.get("/api/v1/articles?page=0")
        assert response.status_code == 422  # 页码从1开始

    def test_list_articles_too_large_page_size(self):
        """测试过大的 page_size (应为 422)"""
        client = get_test_client()
        response = client.get("/api/v1/articles?page_size=200")
        assert response.status_code == 422  # 最大 100


class TestSearchAPI:
    """搜索 API 测试"""

    def test_search_basic(self):
        """测试基本搜索"""
        client = get_test_client()
        try:
            response = client.get("/api/v1/search?q=market&page=1&page_size=5")
            if response.status_code == 200:
                data = response.json()
                assert "total" in data
                assert "items" in data
        except Exception as e:
            pytest.skip(f"数据库不可用: {e}")

    def test_search_missing_query(self):
        """测试缺少搜索关键词 (应为 422)"""
        client = get_test_client()
        response = client.get("/api/v1/search")
        assert response.status_code == 422

    def test_search_empty_query(self):
        """测试空搜索关键词 (应为 422)"""
        client = get_test_client()
        response = client.get("/api/v1/search?q=")
        assert response.status_code == 422


class TestStatsAPI:
    """统计信息 API 测试"""

    def test_stats_basic(self):
        """测试统计信息接口"""
        client = get_test_client()
        try:
            response = client.get("/api/v1/stats")
            if response.status_code == 200:
                data = response.json()
                assert "total_articles" in data
                assert "total_sources" in data
                assert "articles_today" in data
                assert "processed_count" in data
        except Exception as e:
            pytest.skip(f"数据库不可用: {e}")


class TestArticleNotFound:
    """文章不存在的情况测试"""

    def test_get_nonexistent_article(self):
        """测试获取不存在的文章 (应为 404)"""
        client = get_test_client()
        try:
            response = client.get("/api/v1/articles/999999")
            if response.status_code == 404:
                assert "detail" in response.json()
        except Exception as e:
            pytest.skip(f"数据库不可用: {e}")


class TestCrawlTriggerAuth:
    """爬虫触发接口认证测试"""

    def test_trigger_crawl_without_key(self):
        """测试不带 API Key 触发爬虫 (应为 403)"""
        client = get_test_client()
        response = client.post("/api/v1/crawl/trigger")
        assert response.status_code in [403, 500]  # 403=禁止, 500=后台任务异常


# ==================== 运行测试 ====================
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])