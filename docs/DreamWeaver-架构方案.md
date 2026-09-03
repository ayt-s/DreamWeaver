# DreamWeaver — AI 视频生成平台架构方案

> 版本：v1.0（2026-09）
> 技术栈：React（前端）+ Spring Boot 3（Java 业务侧）+ FastAPI（Python 模型侧）
> 模型：agnes-2.5-flash（文本）/ agnes-image-2.5-flash（图像）/ agnes-video-2.5-flash、agnes-video-2.5（视频）

---

## 1. 项目定位

一个多模态 AI 视频生成平台：用户用自然语言（或配上参考图/首尾帧/音频）生成短视频，平台提供剧本→分镜→提示词辅助、任务排队、进度推送、素材管理与历史资产库。

**关键前提**：所有模型推理都在 Agnes AI 云端 API 完成，平台自身**不持有 GPU、不训练模型**。因此 Python FastAPI 是「编排 + 适配」层，不是推理层——这是架构里最重要的定位。

---

## 2. 总体架构

```
┌──────────────────────────────────────────────────────────────┐
│  前端 React (Vite + TS)                                        │
│  生成工作台 · 进度看板(SSE) · 素材库/历史画廊 · 用户中心          │
└───────────────┬──────────────────────────────────────────────┘
                │ REST + SSE
┌───────────────▼──────────────────────────────────────────────┐
│  业务侧 Spring Boot 3 (Java)                                   │
│  · 用户/认证(JWT)  · 任务管理(状态机)  · 素材/资产  · 配额与计费   │
│  · 模型路由(ModelRouter)  · 回调接收  · SSE 推送                 │
│  DB: MySQL          Cache/Queue: Redis        存储: MinIO/OSS   │
└───────────────┬──────────────────────────────────────────────┘
                │ 内部 REST (任务分发 / 回调通知)
┌───────────────▼──────────────────────────────────────────────┐
│  模型侧 FastAPI (Python)                                       │
│  · AgnesGateway: chat/images/videos 统一封装（API Key 只在此层） │
│  · 任务状态机 + video_id 轮询(5s) + 指数退避                    │
│  · 分布式限流(Redis 令牌桶: 视频1/min, 图片分档)                 │
│  · 提示词服务(prompt 增强/中译英)                                │
└───────────────┬──────────────────────────────────────────────┘
                │ HTTPS
┌───────────────▼──────────────────────────────────────────────┐
│  Agnes AI API (apihub.agnes-ai.com)                           │
│  /v1/chat/completions · /v1/images/generations · /v1/videos   │
└──────────────────────────────────────────────────────────────┘
```

### 2.1 为什么这样分（用户已定，做补充论证）

| 层 | 职责 | 决策理由 |
|---|---|---|
| Java 业务侧 | 用户、事务、任务、资产、配额 | 交易型业务逻辑 + 强事务，Spring 生态最稳；支付/多租户后续扩展空间大 |
| Python 模型侧 | 模型 API 封装、轮询、限流、prompt | LLM 工具链和异步 IO（httpx/asyncio/SSE）生态都在 Python；且模型升级只动 FastAPI，不碰业务层 |
| React 前端 | 工作台、实时进度、画廊 | 视频进度这类实时 UI 用 SSE 最省事 |

**解耦核心**：Java 只认「任务 + 产物」，不认模型；FastAPI 只认「模型 + 参数」。模型 API 的任何变化（改用别的供应商、换版本）都收敛在 FastAPI，业务层零改动。

---

## 3. 四个模型的落位

| 模型 | 端点 | 用途 | 关键参数（官方文档核实） |
|---|---|---|---|
| `agnes-2.5-flash` | `POST /v1/chat/completions` | 剧本/分镜生成、中文提示词→英文、参考图理解、失败重试改写 | OpenAI 兼容；context 512K |
| `agnes-image-2.5-flash` | `POST /v1/images/generations` | 文生图/图生图/多图合成：分镜图、参考素材、视频封面 | `size` 档位 `1K/2K/3K/4K` + `ratio`(1:1/3:4/4:3/16:9/9:16/2:3/3:2/21:9)；图生图 `image` 数组放 `extra_body`；`response_format` 必须在 `extra_body` |
| `agnes-video-2.5-flash` | `POST /v1/videos`（异步） | 快速预览版：text/keyframe/reference 三模式 | 仅 `720P`；`seconds` 字符串 `"4"~"12"`；reference 最多 5 图/3 音频，不支持参考视频 |
| `agnes-video-2.5` | `POST /v1/videos`（异步） | 正式成片：text/keyframe/reference，支持参考视频输入 | 同上 + `audios`/`videos` 参考；720P 刊例价 $0.025/秒 |

**查询（两模型都必须）**：`GET /agnesapi?video_id=<ID>&model_name=<模型名>`，5s 轮询。⚠️ 不能用 task_id 查询，否则排队异常拉长；keyframe/reference 模式查询必须带 `model_name`。

**模型路由设计**（Java 侧 `ModelRouter`）：
- 配置驱动（DB 存配比，可灰度）：预览任务 → video-2.5-flash（快），成片任务 → video-2.5（优）。
- 前端工作台提供「快速预览 / 高清成片」两个档位，落库绑定任务。

---

## 4. 核心链路：文生视频

```
前端提交(prompt+时长+画幅+档位)
  → POST /api/tasks/video
  → Java 落库 task(状态=PENDING) → 写 Redis Stream 队列
  → 任务调度器取单 → 调 FastAPI POST /v1/generate/video
  → FastAPI 限流取令牌 → 调 Agnes POST /v1/videos → 拿 video_id
  → FastAPI 持久化 video_id → 后台任务每 5s GET /agnesapi?video_id=…&model_name=…
  → 完成: 下载视频 → MinIO/OSS 落库 → 回调 Java /internal/notify
  → Java 更新任务状态 → SSE 推送前端 (进度/预览/失败原因)
```

**状态机**：`PENDING → QUEUED → SUBMITTED → POLLING → COMPLETED / FAILED / EXPIRED`
- 失败重试：503/429 指数退避（1s→2s→4s→8s，上限 3 次）；参数类 400 直接失败并记录原因。
- 超时兜底：单任务轮询超 15 分钟 → EXPIRED，返还可续单。

**SSE 进度**：`GET /api/tasks/{id}/events`，事件类型 `queued / submitted / polling / completed / failed`，前端进度条 + 失败原因直接展示，无需轮询。

---

## 5. 关键设计点

### 5.1 限流是硬约束（免费档）
- 视频：1 个/分钟 → Redis 分布式令牌桶，**全局共享**（多实例也必须一致）；队列 FIFO + 优先级（会员优先）。
- 图片：1K/2K/3K/4K 分档限流，2K 10/min、3K/4K 1/min。
- 限流在 FastAPI 网关层做第一道，Java 层做配额（用户维度）第二道。

### 5.2 产物必须落库
- Agnes 返回的图片/视频 URL 有时效（任务完成后可能失效）。完成即下载 → 对象存储（自建 MinIO 或阿里 OSS），前端永远可访问。
- 素材上传（图生视频的参考图/音频）同样入 MinIO，生成**预签名 URL** 传给模型（需公网可访问）。

### 5.3 Prompt 工作台（agnes-2.5-flash 价值点）
- 「一句话想法 → 剧本 → 分镜 → 英文视频提示词」模板化一键生成，内置俞高质量 Prompt 模板（主体+动作+场景+镜头运动+光照+风格）。
- 中文 prompt 自动翻译成英文提交（视频模型英文更稳）。

### 5.4 安全
- Agnes API Key **只存在 FastAPI 环境变量/密钥服务**，前端与 Java 都拿不到。
- 出参校验：返回内容做长度/类型/链接校验；后续可加内容审核（图片/视频 NSFW 识别）。
- 用户配额（每日次数/秒数），超额排队或拒绝。

### 5.5 可拔插（对齐供应商多态）
- `AgnesGateway` 接口化：`chat() / image() / video_submit() / video_query()`，底层 HTTP 客户端可换；
- 模型名/端点/限流参数全部 `config.yaml` + DB 双驱动，换模型族（如未来接自有推理、换火山/即梦）不动业务代码。

---

## 6. 模块划分与表设计（骨架）

**Spring Boot 模块**（单工程多包即可，暂不拆微服务）：
```
com.dreamweaver
 ├── auth        # JWT 登录注册
 ├── task        # 任务状态机 / 队列入口 / SSE
 ├── asset       # 素材上传、媒体落库、预签名
 ├── modelroute  # ModelRouter 配置与路由
 ├── quota       # 用户配额
 └── user        # 用户资料
```

**FastAPI 目录**：
```
app/
 ├── gateway/    # AgnesGateway(chat/image/video 封装)
 ├── worker/     # video_id 轮询任务、重试、退避
 ├── ratelimit/  # Redis 令牌桶
 ├── prompt/     # 剧本/分镜/翻译服务
 ├── notify/     # 回调 Java
 └── main.py     # /v1/generate/* 路由
```

**核心表**：
- `user`（id, username, password_hash, quota_day, role）
- `task`（id, user_id, type[VIDEO/IMAGE/TEXT], model, status, params JSON, video_id, result_url, error, created_at）
- `media_asset`（id, task_id, type[VIDEO/IMAGE], object_key, url, size, meta JSON）
- `model_config`（id, model_name, purpose, enabled, weight, rpm_limit, priority）

---

## 7. API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/auth/register` `/api/auth/login` | 注册登录（JWT） |
| POST | `/api/tasks/video` | 提交生成任务（prompt/秒数/画幅/档位/素材 id 列表） |
| POST | `/api/tasks/image` | 文生图/图生图 |
| GET | `/api/tasks/{id}` | 任务详情 |
| GET | `/api/tasks/{id}/events` | SSE 进度流 |
| GET | `/api/tasks?page=` | 历史列表 |
| POST | `/api/assets/upload` | 素材上传（图生视频参考图/音频） |
| GET | `/api/assets/{id}/presign` | 预签名 URL（模型侧用） |
| POST | `/v1/generate/video` `/v1/generate/image` `/v1/generate/prompt` | FastAPI 内部接口（Java 只调这三个） |
| POST | `/internal/notify` | FastAPI → Java 完成回调 |

---

## 8. 实施路线

| 阶段 | 范围 | 预估 |
|---|---|---|
| Phase 1 骨架+主链路 | 工程三端搭建、文生视频/文生图全链路、轮询+SSE、MinIO 落库、简单画廊 | 1~2 周 |
| Phase 2 素材与模式 | 图生视频、keyframe 首尾帧、reference 模式（图/音频）、素材上传、用户/配额/登录 | 1~2 周 |
| Phase 3 体验与规模 | Prompt 工作台（剧本→分镜→提示词+中译英）、批量生成、多 Key 池轮换、成本统计、内容审核 | 2~3 周 |

---

## 9. 风险与对策

| 风险 | 对策 |
|---|---|
| 免费档视频 1/min → 用户并发排队 | 全局限流 + 队列 + 前端可见排队序号；多 API Key 池（企业档 RPM 更高） |
| 视频排队 >5min 且不返回 | 排查是否误用 task_id 查询；一律 video_id + model_name |
| 任务 URL 失效 | 完成即下载落 MinIO/OSS |
| 429/503/500 | 指数退避重试；500 多为参数类（尺寸/模式不匹配），先做过参数校验层 |
| 中文提示词效果差 | prompt 服务统一中译英（agnes-2.5-flash） |
| 文本模型端点细节变化 | Gateway 接口化，2.5 系列端点以 platform 模型列表为准，接入期先用 curl 冒烟 |

---

## 10. 需要你确认的决策点

1. **用户系统是否要**：Phase 1 可以单机单用户（内网使用），登录/配额放到 Phase 2。
2. **视频时长档位**：默认 5s 起步，UI 上给 4/5/7/10s 四档（flash 与 2.5 都支持 4~12s）。
3. **画幅**：默认 16:9，可选的 9:16（短视频）、1:1（信息流）。
4. **对象存储**：本机 MinIO（Docker 一键起）还是直接阿里云 OSS。
5. **Agnes API Key**：你自己到 platform.agnes-ai.com 配置，代码里全部走环境变量。

> 附：模型能力与参数均已对照 Agnes 官方文档（agnes-video-2.5 / video-2.5-flash / image-2.5-flash 页），关键差异（flash 仅 720P、reference 限 5 图 3 音频、不支持参考视频）已纳入 5.1 校验层。