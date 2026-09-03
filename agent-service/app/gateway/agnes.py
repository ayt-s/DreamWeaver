"""Agnes AI 模型网关（Phase 1 最小实现）。

封装 chat / 提交视频 / 查询视频三个端点，接口化以便未来换供应商。
视频相关约束（来自官方文档，已核实）：
- 异步任务，创建后必须用 video_id 查询（绝不用 task_id）
- 查询：GET /agnesapi?video_id=<ID>&model_name=<模型>
- seconds 为字符串 "4"~"12"；size 仅 "720P"；n 固定 1
"""
import time
from typing import Any

import httpx

from app.config import settings


class AgnesGateway:
    def __init__(self) -> None:
        self.base_url = settings.agnes_base_url
        self.headers = settings.headers
        self._client = httpx.AsyncClient(
            base_url=self.base_url, headers=self.headers, timeout=60.0
        )

    # ---------- 文本 ----------
    async def chat(self, prompt: str, model: str | None = None,
                   temperature: float = 0.2, max_tokens: int = 4096) -> str:
        """调用文本模型，返回纯文本内容（Phase 1 用简单形式，结构化输出后续加）。"""
        resp = await self._client.post("/chat/completions", json={
            "model": model or settings.text_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        })
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    # ---------- 视频 ----------
    async def submit_video(self, prompt: str, model: str | None = None,
                           seconds: str | None = None,
                           aspect_ratio: str | None = None,
                           mode: str = "text",
                           reference_images: list[str] | None = None) -> dict[str, Any]:
        """提交视频生成任务，返回包含 video_id / model_name 的 dict。"""
        payload: dict[str, Any] = {
            "model": model or settings.video_model_fast,
            "prompt": prompt,
            "mode": mode,
            "seconds": seconds or settings.default_seconds,
            "size": "720P",
            "aspect_ratio": aspect_ratio or settings.default_aspect_ratio,
            "n": 1,
        }
        if reference_images:
            # 图生视频/参考模式：图片需公网可访问 URL
            payload["images"] = reference_images
        resp = await self._client.post("/videos", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return {
            "video_id": data["id"],
            "model_name": payload["model"],
        }

    async def query_video(self, video_id: str, model_name: str,
                          mode: str = "text") -> dict[str, Any]:
        """查询视频任务状态（实测 2026-09：返回含 progress 百分比、internal_status）。

        ⚠️ 实测确认：本版本 API 的 id/video_id/task_id 为同值（task_xxx 格式），
        统一用 video_id 参数查询 + 显式带 model_name，不要走 task_id 查询路径。
        """
        resp = await self._client.get(
            "/agnesapi",
            params={"video_id": video_id, "model_name": model_name},
        )
        resp.raise_for_status()
        return resp.json()

    async def close(self) -> None:
        await self._client.aclose()


gateway = AgnesGateway()