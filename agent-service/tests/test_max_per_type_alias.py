"""#35：锚定图接口要同时认 `max_per_type` 与驼峰的 `maxPerType`（2026-09-19 修）。

前端 `web-frontend/src/api/novelAnchors.ts` 发的是 `maxPerType`，而 agent 侧只声明了
`max_per_type` ⇒ pydantic 默认忽略未知字段，**传了不生效**（静默按默认 5 张生成）。
目前没有调用方传值（休眠缺陷），但字段名在层间不一致迟早会咬人 —— 两种拼写都要认。
"""
from app.controller.novel_anchors_api import NovelAnchorsRequest


def test_accepts_snake_case():
    assert NovelAnchorsRequest(max_per_type=3).max_per_type == 3


def test_accepts_camel_case_from_frontend():
    """前端实际发的拼写 —— 这就是原来被静默丢掉的那个。"""
    assert NovelAnchorsRequest(maxPerType=3).max_per_type == 3


def test_default_is_five():
    assert NovelAnchorsRequest().max_per_type == 5
