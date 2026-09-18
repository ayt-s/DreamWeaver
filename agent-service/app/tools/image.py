"""图像生成工具（Phase 4 P0）：封装 gateway.generate_image()。"""
import logging

from app.gateway.agnes import gateway

logger = logging.getLogger(__name__)


async def generate_image_tool(prompt: str, size: str | None = None,
                              ratio: str | None = None) -> list[str]:
    """调用图像 API，返回图片 URL 列表。

    `size` / `ratio` 建议显式传：不传时服务端按 1:1 出图（实测 1024x1024），
    与 16:9 的视频链路不匹配。见 gateway.generate_image 的说明。
    """
    urls = await gateway.generate_image(prompt=prompt, size=size, ratio=ratio)
    logger.info("generate_image_tool 完成: %d 张图片", len(urls))
    return urls
