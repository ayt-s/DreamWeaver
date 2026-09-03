"""图像生成工具（Phase 4 P0）：封装 gateway.generate_image()。"""
import logging

from app.gateway.agnes import gateway

logger = logging.getLogger(__name__)


async def generate_image_tool(prompt: str) -> list[str]:
    """调用图像 API，返回图片 URL 列表。"""
    urls = await gateway.generate_image(prompt=prompt)
    logger.info("generate_image_tool 完成: %d 张图片", len(urls))
    return urls
