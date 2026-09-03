# DreamWeaver — AI 短视频创作 Agent（面试个人项目版 v2）

> 版本：v2.0（2026-09）。本版将项目定位为 **Agent 项目**，取代 v1 的「任务平台」定位。
> 模型：agnes-2.5-flash（Agent 大脑/多角色文本）/ agnes-image-2.5-flash（图像）/ agnes-video-2.5-flash、agnes-video-2.5（视频）
> 面试定位：把「晟星实习的 Agent 平台经验（动态工具注册、LangGraph 编排、评测闭环、失败回流）」做成一个独立、可演示、可深挖的完整项目。

---

## 1. 新定位：一句话

> **用户一句话需求 → AI 导演 Agent 自动产出短视频成片**，中间经过「剧本 → 分镜 → 提示词 → 素材生成 → 视频生成 → 质检 → 修正」的完整 Agent 闭环。

这不是"调视频 API 的工具站"，而是一个 **多 Agent 协作 + 工具调用 + 评测闭环** 的创作系统。与简历经验一一对应：

| 简历已有经验（晟星） | DreamWeaver 中的体现 |
|---|---|
| LangGraph 多节点编排 + 状态持久化 | 创作流水线就是 LangGraph DAG：需求解析→剧本→分镜→生成→质检→回流 |
| 动态工具注册 / MCP 中台 | Agent 通过工具注册表调用 image/video 生成，白名单 + 全链路调用审计 |
| Text-to-SQL 评测闭环 + 失败回放 | QC Agent 质检 + 错误回流 + 样本回放，改 Prompt 先过评估集 |
| Agent Harness（Prompt 模板版本化、轨迹记录） | 创作会话全轨迹持久化 + 模板版本化 + 断点恢复 |
| SQL Guardrail 安全链路 | 生成参数合规校验、内容审核、出参校验（不直接写死规则，由能力目录驱动） |

面试叙事：**"我把实习里做的 Agent 平台方法论，独立完整地重做了一遍，落地到一个具体场景（AI 短视频创作），三端都是自己写的。"**

---

## 2. Agent 核心编排（LangGraph DAG）

```
用户一句话需求
   │
   ▼
┌─ requirement_parser（agnes-2.5-flash）
│   需求 → 结构化 Brief：主题/风格/时长/受众/参考素材引用
│   （结构化输出，Pydantic 强校验，失败追问澄清）
│
▼
┌─ script_writer（agnes-2.5-flash）
│   Brief → 剧本：分镜列表（每镜：画面/动作/镜头运动/时长/旁白可选）
│   （注入 Prompt 模板版本号，模板改动留痕）
│
▼
┌─ storyboarder（agnes-2.5-flash）
│   剧本 → 每镜英文提示词 + 生成参数（mode/seconds/aspect_ratio/参考素材id）
│   （调用 LLM 工具：中译英；结构化 JSON 输出）
│
▼
┌─ asset_supplier（可选分支）
│   按需调 generate_image 工具：分镜参考图/关键帧/封面
│
▼
┌─ video_generator ──┬─ 预览：agnes-video-2.5-flash（720P，快）
│                    └─ 成片：agnes-video-2.5（优）
│   调 generate_video 工具 → 异步提交 → 轮询 video_id（5s）
│
▼
┌─ qc_agent（规则 + LLM 判分双通道）
│   规则层：参数合法性（≤720P、seconds=4~12格式、reference 素材数限制、宽高比白名单）
│   LLM 层：与剧本一致性 / 镜头连贯性 / 内容合规打分
│   │
│   ├─ 通过 ──► synthesizer（多镜拼接 FFmpeg + 封面）+ 产物落库 + 轨迹归档
│   └─ 失败 ──► fix_loop：按错误类型改写提示词/参数 → 回到 storyboarder 重生成
│                （同一需求最多 N 轮，全程记录修正链：哪版 Prompt、为什么失败、改了什么）
│
▼
HITL 人工审核节点（敏感内容/关键帧人工确认，非必须路径）
```

**状态持久化与恢复**：每个节点产出（Brief/剧本/分镜/生成参数/视频结果/QC 报告）全部落库，session 级可恢复——worker 崩溃后重启扫描未完成任务，从断点接管轮询（对应 v1 审查里 review 的「重接管」要求，Agent 长任务更需要）。失败重试、幂等回调、Redis 锁在此兑现。

---

## 3. 工具注册表（MCP 风格，对齐简历 MCP 中台经验）

```
ToolDefinition[]:
  generate_image(prompt, size, ratio, image_refs?)        → agnes-image-2.5-flash
  generate_video(prompt, seconds, mode, aspect_ratio,
                 first_frame?, last_frame?, images?, audios?)
                                                          → agnes-video-2.5-flash / 2.5
  query_video_status(video_id)                            → 内部轮询工具（Agent 感知异步状态）
  search_user_assets(keyword)                             → Java 业务侧（用户素材库检索）
  save_asset(media)                                       → Java 业务侧（产物落库）
```

- 注册表存 DB：name / description / input schema / 安全策略（白名单、超时、频控）/ 发布状态——与实习的「动态 Tool 元模型」同一套方法论。
- **每轮工具调用全链路审计**：谁调的、参数是什么、耗时、成功失败，前端轨迹可视化直接展示（面试演示点）。
- 能力约束不写死在 Java：FastAPI 暴露 `GET /v1/capabilities`（各模型可用 mode/参数约束/档位），Java 启动拉取缓存做入参校验，换模型族 Java 零改动（采纳 review 的边界方案）。

---

## 4. 评测闭环（面试最深的深挖点）

每个生成任务写一条完整记录：`需求 → Brief → 剧本 → 分镜提示词 → 生成参数 → 生成结果 → QC 分数 → 错误原因 → 修正链`。

- **评测集**：预设 N 个典型创作需求（不同风格/时长/模式），改 Prompt 模板或节点逻辑前先全量跑一遍，达标才发布。
- **失败样本回放**：对同一需求重新执行，对比修正前后 QC 分数是否提升——直接证明「回流逻辑有效」，这是不做 Agent 拿不出来的数据。
- **QC 判分明确**：规则判分（参数类）+ LLM 判分（内容一致性类）分两类记录，面试被问"QC 是规则还是 LLM"能直接答实现细节（呼应简历面试准备）。

---

## 5. 架构调整（相对 v1）

```
React 前端：创作工作台 + Agent 轨迹可视化（每节点状态/中间产物/工具调用审计流）+ 画廊
     │
Spring Boot 3：用户/配额/创作会话管理/资产/审计查询（Agent 调用的业务工具落在这里，见 §3）
     │  REST + SSE
FastAPI（重写为 Agent 执行底座）：
     ├─ Agent 编排：LangGraph DAG（§2）+ 状态持久化 + 断点恢复
     ├─ 工具层：注册表 + 白名单 + 审计 + 限流（Redis 令牌桶：视频 1/min）
     ├─ 模型网关：AgnesGateway(chat/image/video) + video_id 轮询 + 指数退避
     └─ 能力目录 /v1/capabilities + QC 评测
     │  HTTPS
Agnes API（4 个模型，参数约束以官方文档为准：flash 仅 720P、seconds 字符串 4~12、
  reference 限 5 图/3 音频、查询必须 video_id+model_name）
```

**简化点（个人项目不值得做的）**：不做微服务、不做多租户、不做支付；单用户 + 配额即可。**做厚的**：Agent 轨迹记录、评测闭环、断点恢复——这三样是面试讲故事的素材。

---

## 6. 实施路线（Agent 版）

| 阶段 | 内容 |
|---|---|
| Phase 1（1~2 周） | 三端骨架 + **单 Agent 线性链路**：需求→剧本→分镜→视频生成（text 模式）→ 完成；SSE 轨迹推送；MinIO 落库 |
| Phase 2（1~2 周） | 工具注册表 + 审计 + 多模式（keyframe/reference）+ 素材上传 + QC 规则层 + fix_loop |
| Phase 3（2 周） | QC LLM 判分 + 评测集 + 失败回放 + 断点恢复 + 轨迹可视化 + 批量创作 |

MVP 建议：**Phase 1 就把「Agent」两个字坐实**——哪怕只走 text 模式，也必须包含「剧本→分镜→提示词生成→工具调用→结果反馈」的闭环，而不是「一个接口把 prompt 传给视频 API」。

---

## 7. 面试口径准备（必读）

**讲述主线（90 秒版）**：实习里我做了 MCP 工具中台和 Text-to-SQL Agent 的评测闭环 → 这份工作让我意识到 Agent 平台的通用方法论 → 于是用这套方法论独立做了一个垂直场景项目：AI 短视频创作 Agent，三端全栈实现，重点在 Agent 编排、工具治理和评测闭环。

**会被深挖的问题（提前准备）**：
1. 为什么用 LangGraph 而不是自己写状态机？（答：节点粒度、状态持久化、可恢复是 LangGraph 天生支持的；但要能讲清每个节点的状态 Schema 是自己设计的）
2. QC 判断标准谁定的？规则还是 LLM？（答：两层，参数类规则、内容类 LLM 判分，分数进评测集）
3. 失败回流怎么证明有效？（答：评测集 + 样本回放对比修正前后分数）
4. 为什么模型全走云端 API？（答：Agent 项目重点是编排与工程化，推理是可拔插后端；这也复用了我实习的 MCP 方法论，把生成能力封装成统一工具）
5. 万一 Agnes API 挂了怎么办？（答：指数退避 + 断点恢复 + 幂等回调，任务不丢）

**红线**：不谈「我训练了模型」（没有）；不夸大并发能力（免费档视频 1/min，限流是设计的一部分不是缺陷）；所有实现细节必须能和代码对上（沿用「口径冲突以源码为准」原则）。

---

## 8. 待确认决策

1. **Agent 编排用 LangGraph 还是自研轻量编排**：LangGraph 快但面试官可能追问「是不是套模板」；自研（简单 DAG + 状态机）更显功底但慢。推荐 LangGraph + 讲清状态设计。
2. **多 Agent 要不要真做**：v2 里「导演/剧本/分镜/质检」是四个节点角色，可以做成四个独立 Agent（各自 context + 工具集）或一个主 Agent 多轮工具调用。推荐前者——「多 Agent 协作」是简历关键词。
3. **QC LLM 判分用哪个模型**：agnes-2.5-flash（复用大脑，不额外花钱）。
4. 其余沿用 v1 决策（MinIO vs OSS、用户系统是否要）。

> 附：v1 的审查结论（状态机粒度、SSE 协议、worker 接管、能力目录、幂等回调、MinIO 内网坑）在 Agent 版依然全部适用，已合入 §2/§3/§7 对应位置；完整细节见 `DreamWeaver-架构审查.md` 与 `DreamWeaver-架构方案.md`（v1）。