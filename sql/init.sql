-- ============================================================
-- GFNA (全球财经新闻聚合器) 数据库初始化脚本
-- 数据库: PostgreSQL 16
-- 字符集: UTF-8 (默认)
-- 说明: 请以 postgres 用户运行此脚本创建数据库和表
-- 运行方式:
--   psql -U postgres -f sql/init.sql
--   或者用 Docker:
--   docker exec -i gfna-pg psql -U postgres < sql/init.sql
-- ============================================================

-- 1. 创建数据库（如果不存在）
-- 注意: PostgreSQL 不能在事务块中创建数据库，需要在脚本外执行
-- CREATE DATABASE gfna_db;

-- 使用数据库
-- \c gfna_db

-- ============================================================
-- 2. 建表: news_sources (新闻来源表)
-- ============================================================
CREATE TABLE IF NOT EXISTS news_sources (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(100) NOT NULL,
    base_url        VARCHAR(500) NOT NULL,
    country         VARCHAR(50) DEFAULT NULL,
    language        VARCHAR(20) DEFAULT 'en',
    is_active       SMALLINT DEFAULT 1,         -- PostgreSQL 没有 TINYINT
    crawl_interval  INT DEFAULT 60,
    created_at      TIMESTAMPTZ DEFAULT NOW(),   -- TIMESTAMP WITH TIME ZONE
    updated_at      TIMESTAMPTZ DEFAULT NOW(),

    CONSTRAINT uk_name UNIQUE (name)
);

COMMENT ON TABLE news_sources IS '新闻来源表：记录各个财经媒体的基本信息';
COMMENT ON COLUMN news_sources.is_active IS '1=启用, 0=停用';
COMMENT ON COLUMN news_sources.crawl_interval IS '抓取间隔 (分钟)';


-- ============================================================
-- 3. 建表: news_articles (新闻文章表 - 核心表)
-- ============================================================
CREATE TABLE IF NOT EXISTS news_articles (
    id              SERIAL PRIMARY KEY,
    source_id       INT NOT NULL REFERENCES news_sources(id)
                        ON DELETE RESTRICT ON UPDATE CASCADE,
    title           VARCHAR(500) NOT NULL,
    url             VARCHAR(2048) NOT NULL,
    url_hash        CHAR(64) NOT NULL,
    content         TEXT DEFAULT NULL,
    summary         TEXT DEFAULT NULL,           -- AI 生成的中文摘要
    author          VARCHAR(200) DEFAULT NULL,
    publish_time    TIMESTAMPTZ DEFAULT NULL,
    crawl_time      TIMESTAMPTZ DEFAULT NOW(),
    sentiment_score DECIMAL(3,2) DEFAULT NULL,  -- 0=负面, 1=中性, 2=正面
    is_processed    SMALLINT DEFAULT 0,          -- 0=未处理, 1=已完成, 2=失败
    created_at      TIMESTAMPTZ DEFAULT NOW(),

    -- URL 哈希唯一索引 (防重复)
    CONSTRAINT uk_url_hash UNIQUE (url_hash),

    -- 全文搜索: PostgreSQL tsvector + GIN 索引
    -- 使用 simple 配置支持中英文混合搜索
    CONSTRAINT fk_article_source
        FOREIGN KEY (source_id) REFERENCES news_sources(id)
        ON DELETE RESTRICT ON UPDATE CASCADE
);

-- 创建复合索引加速查询
CREATE INDEX IF NOT EXISTS idx_articles_publish_time ON news_articles(publish_time DESC);
CREATE INDEX IF NOT EXISTS idx_articles_crawl_time ON news_articles(crawl_time DESC);
CREATE INDEX IF NOT EXISTS idx_articles_source_id ON news_articles(source_id);
CREATE INDEX IF NOT EXISTS idx_articles_is_processed ON news_articles(is_processed);

-- 【关键】PostgreSQL 全文搜索索引
-- tsvector 把文本变成词条向量，GIN 索引实现高效全文检索
-- 'simple' 配置不做语言分析（中英文混合场景下效果最好）
CREATE INDEX IF NOT EXISTS idx_articles_fts
    ON news_articles
    USING GIN (to_tsvector('simple', coalesce(title, '') || ' ' || coalesce(content, '')));

COMMENT ON TABLE news_articles IS '新闻文章表：存储每篇财经新闻及其AI摘要';
COMMENT ON COLUMN news_articles.url_hash IS 'URL 的 SHA-256 哈希值 (用于去重)';
COMMENT ON COLUMN news_articles.is_processed IS '摘要状态: 0=未处理, 1=已完成, 2=失败';
COMMENT ON COLUMN news_articles.sentiment_score IS '情感分析分数 (0=负面, 1=中性, 2=正面)';


-- ============================================================
-- 4. 建表: crawl_logs (爬虫日志表)
-- ============================================================
-- PostgreSQL 使用自定义类型代替 MySQL ENUM
CREATE TYPE crawl_status AS ENUM ('running', 'success', 'failed');

CREATE TABLE IF NOT EXISTS crawl_logs (
    id              SERIAL PRIMARY KEY,
    source_id       INT DEFAULT NULL,
    status          crawl_status DEFAULT 'running',
    articles_found  INT DEFAULT 0,
    articles_new    INT DEFAULT 0,
    error_message   TEXT DEFAULT NULL,
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    finished_at     TIMESTAMPTZ DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_logs_source_id ON crawl_logs(source_id);
CREATE INDEX IF NOT EXISTS idx_logs_status ON crawl_logs(status);
CREATE INDEX IF NOT EXISTS idx_logs_started_at ON crawl_logs(started_at);

COMMENT ON TABLE crawl_logs IS '爬虫日志表：记录每次抓取的运行情况';


-- ============================================================
-- 5. 初始化数据：插入默认的新闻源
-- ============================================================
INSERT INTO news_sources (name, base_url, country, language, crawl_interval) VALUES
  ('Reuters',     'https://www.reuters.com/business/',     'UK',  'en', 60),
  ('Bloomberg',   'https://www.bloomberg.com/markets',     'US',  'en', 60),
  ('CNBC',        'https://www.cnbc.com/world/',           'US',  'en', 60),
  ('MarketWatch', 'https://www.marketwatch.com/latest-news','US', 'en', 60)
ON CONFLICT (name) DO NOTHING;

-- ============================================================
-- 6. 触发器: 自动更新 updated_at 字段
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 为 news_sources 表添加自动更新触发器
DROP TRIGGER IF EXISTS trg_sources_updated_at ON news_sources;
CREATE TRIGGER trg_sources_updated_at
    BEFORE UPDATE ON news_sources
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- ============================================================
-- 脚本执行完毕
-- ============================================================