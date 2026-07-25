"""
DeepSeek AI 摘要服务 (llm_service.py)
------------------------------------
【核心模块】使用 DeepSeek API 将英文财经新闻总结为中文摘要。

技术说明:
  - 使用 openai 库的兼容模式调用 DeepSeek (因为 DeepSeek API 与 OpenAI 接口兼容)
  - 包含重试机制: API 调用失败时自动重试，最多 3 次
  - 包含降级处理: 所有重试都失败时返回空字符串，不阻塞爬虫流程

为什么用 DeepSeek？
  - 中文效果好: DeepSeek 对中文的理解和生成能力非常优秀
  - 性价比高: API 价格远低于 GPT-4/Claude
  - 兼容性强: 完全兼容 OpenAI SDK，切换成本低

环境变量依赖:
  - DEEPSEEK_API_KEY: DeepSeek API 密钥 (必填)
  - DEEPSEEK_BASE_URL: API Base URL，默认 https://api.deepseek.com
  - DEEPSEEK_MODEL: 模型名，默认 deepseek-chat
"""
import time
import logging
from openai import OpenAI
from app.core.config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    LLM_MAX_TOKENS,
    LLM_TEMPERATURE,
    LLM_MAX_RETRIES,
)

# 配置日志
logger = logging.getLogger(__name__)


# ==================== 初始化 DeepSeek 客户端 ====================
# 使用 OpenAI SDK 兼容模式连接 DeepSeek
# 注意：DeepSeek 的 API 和 OpenAI 接口完全兼容，只需要改 base_url
client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
)


# ==================== 系统提示词 ====================
# 这个提示词定义了 AI 的角色和行为规范
SYSTEM_PROMPT = (
    "你是一位专业的财经新闻编辑，拥有20年金融分析经验。"
    "你的任务是将英文财经新闻提炼为简洁、客观、专业的中文摘要。"
    "请遵循以下原则："
    "1. 提取最重要的3-5个核心观点"
    "2. 保留关键数字和数据（涨跌幅、金额、指数等）"
    "3. 用简练的专业语言，避免口语化表达"
    "4. 保持客观中立，不添加主观评论"
    "5. 字数限制在200字以内"
)


def _build_user_prompt(text: str) -> str:
    """
    构建发送给 DeepSeek 的用户提示词

    参数:
        text: 需要总结的英文新闻正文

    返回:
        格式化的用户提示词字符串
    """
    return (
        "请将以下财经新闻总结为中文，限制在200字以内，保持客观专业：\n\n"
        f"{text}"
    )


def generate_summary(
    text: str,
    max_retries: int = None,
) -> str:
    """
    调用 DeepSeek API 生成中文摘要 (带重试机制)

    参数:
        text: 需要总结的新闻正文（英文）
        max_retries: 最大重试次数，默认使用配置文件中的 LLM_MAX_RETRIES

    返回:
        中文摘要字符串 (200字以内)
        如果所有尝试都失败，返回空字符串 ""

    工作原理:
        1. 向 DeepSeek 发送 system prompt + user prompt
        2. 等待 AI 生成中文摘要
        3. 如果失败，等待一段时间后重试 (指数退避策略)
        4. 所有尝试都失败后，记录日志并返回空字符串

    调用示例:
        >>> summary = generate_summary("The S&P 500 rose 2.3% on Friday...")
        >>> print(summary)
        "标普500指数周五上涨2.3%，科技股领涨..."
    """
    # 使用配置的默认重试次数
    if max_retries is None:
        max_retries = LLM_MAX_RETRIES

    # 如果 API Key 未配置，直接返回提示信息
    if not DEEPSEEK_API_KEY or DEEPSEEK_API_KEY.startswith("your-"):
        logger.warning("DeepSeek API Key 未配置，跳过摘要生成")
        return "【API Key 未配置，请在 .env 文件中设置 DEEPSEEK_API_KEY】"

    # 如果输入文本为空或太短，不需要总结
    if not text or len(text.strip()) < 50:
        logger.info("文本太短，跳过摘要生成")
        return "内容过短，无需总结"

    last_error = None

    # 重试循环
    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"调用 DeepSeek API (第 {attempt}/{max_retries} 次)...")

            # ========== 核心 API 调用 ==========
            response = client.chat.completions.create(
                model=DEEPSEEK_MODEL,          # 模型名称: deepseek-chat
                messages=[
                    {
                        "role": "system",
                        "content": SYSTEM_PROMPT
                    },
                    {
                        "role": "user",
                        "content": _build_user_prompt(text)
                    },
                ],
                max_tokens=LLM_MAX_TOKENS,      # 最大输出 token 数
                temperature=LLM_TEMPERATURE,    # 温度 (0.3 = 偏保守，适合新闻)
            )

            # 从响应中提取摘要文本
            summary = response.choices[0].message.content

            # 清理前后空白字符
            summary = summary.strip() if summary else ""

            if summary:
                logger.info(f"摘要生成成功，长度: {len(summary)} 字")
                return summary
            else:
                logger.warning("DeepSeek 返回了空摘要，将重试...")

        except Exception as e:
            last_error = e
            logger.error(f"DeepSeek API 调用失败 (第 {attempt} 次): {e}")

            # 如果不是最后一次，等待后重试 (指数退避: 2秒, 4秒, 8秒...)
            if attempt < max_retries:
                wait_seconds = 2 ** attempt  # 指数退避
                logger.info(f"等待 {wait_seconds} 秒后重试...")
                time.sleep(wait_seconds)

    # 所有重试都失败 — 记录错误日志，返回空字符串
    logger.error(
        f"DeepSeek API 调用全部 {max_retries} 次尝试均失败。"
        f"最后错误: {last_error}"
    )
    return ""


def generate_batch_summaries(
    articles: list,
    batch_size: int = 5,
) -> dict:
    """
    批量生成摘要 (给多篇文章依次生成摘要)

    参数:
        articles: 文章对象列表 (需要有 id 和 content 属性)
        batch_size: 每批处理的文章数 (控制并发压力)

    返回:
        dict: {article_id: summary_string} 的映射
    """
    results = {}
    total = len(articles)

    logger.info(f"开始批量生成摘要，共 {total} 篇文章")

    for i, article in enumerate(articles):
        logger.info(f"处理进度: {i+1}/{total} (文章ID: {article.id})")

        # 只处理有内容的文章
        if not article.content:
            logger.info(f"文章 {article.id} 无内容，跳过")
            results[article.id] = ""
            continue

        # 调用单篇摘要生成
        summary = generate_summary(article.content)
        results[article.id] = summary

        # 批次间短暂休息，避免触发 API 限流
        if (i + 1) % batch_size == 0 and i + 1 < total:
            logger.info(f"已处理 {batch_size} 篇，休息 2 秒...")
            time.sleep(2)

    success_count = sum(1 for s in results.values() if s)
    logger.info(f"批量摘要完成: {success_count}/{total} 篇生成成功")

    return results