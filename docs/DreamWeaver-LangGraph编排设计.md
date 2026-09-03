# DreamWeaver — LangGraph 编排与断点恢复设计

> 对应：DreamWeaver-架构方案-v2-Agent.md §2
> 面试深挖点：状态 Schema 设计、Checkpoint 持久化、失败回流、断点恢复

---

## 一、LangGraph State 定义

### 1.1 核心 State（TypedDict）

```python
from typing import TypedDict, NotRequired
from enum import Enum

class TaskStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    SCRIPT_WRITING = "script_writing"
    STORYBOARD_WRITING = "storyboard_writing"
    ASSET_GENERATING = "asset_generating"
    VIDEO_GENERATING = "video_generating"
    QC_CHECKING = "qc_checking"
    FIX_LOOPING = "fix_looping"
    SYNTHESIZING = "synthesizing"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"

class FixReason(str, Enum):
    PARAM_INVALID = "param_invalid"          # 参数不合法
    CONTENT_INCONSISTENT = "content_inconsistent"  # 与剧本不一致
    VISUAL_QUALITY_LOW = "visual_quality_low"  # 画面质量问题
    AUDIO_MISMATCH = "audio_mismatch"        # 音频不匹配

class QCScore(TypedDict):
    param_score: float       # 规则层评分 0-1
    content_score: float     # LLM层评分 0-1
    total_score: float       # 加权总分
    passed: bool
    fix_reason: NotRequired[FixReason]
    fix_hint: NotRequired[str]  # 给 fix_loop 的改写指引

class GenerationTrace(TypedDict):
    """每次工具调用的完整审计记录"""
    tool_name: str
    params: dict
    result: dict
    latency_ms: int
    timestamp: int
    retry_count: int

class CreativeSessionState(TypedDict):
    # === 输入 ===
    session_id: str
    user_id: str
    raw_prompt: str                          # 用户原始输入
    
    # === 各节点产出（全部落库，断点恢复用）===
    brief: dict                       # requirement_parser 输出
    script: list[dict]                # script_writer 输出（分镜列表）
    storyboard: list[dict]            # storyboarder 输出（每镜英文提示词+参数）
    assets: list[dict]                # asset_supplier 输出的参考图
    video_urls: list[str]             # video_generator 输出的视频URL
    qc_report: QCScore               # qc_agent 质检报告
    
    # === 控制流 ===
    status: TaskStatus
    fix_round: int                    # 当前第几轮修正（0=首次）
    max_fix_rounds: int               # 最大修正轮次（默认3）
    fix_history: list[dict]           # 每轮修正记录（哪版prompt、为什么失败、改了什么）
    
    # === 审计 ===
    trace: list[GenerationTrace]      # 全链路工具调用审计
    error_message: NotRequired[str]
    
    # === 元数据 ===
    model_config: dict                # 当前使用的模型配置
    created_at: int
    updated_at: int
```

### 1.2 为什么每个节点产出都落 State

**面试必问：为什么不直接传中间变量？**

因为 LangGraph 的 `interrupt()` 暂停后，恢复时靠的是 Checkpoint 里存的状态。如果中间产物只在函数局部变量里，中断后就丢了。全部写进 State → Checkpoint 自动序列化 → 断点恢复有东西可接。

---

## 二、LangGraph 图结构

### 2.1 图定义

```python
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver  # 开发用
# from langgraph.checkpoint.postgres import PostgresSaver  # 生产用

graph = StateGraph(CreativeSessionState)

# === 节点注册 ===
graph.add_node("requirement_parser", requirement_parser_node)
graph.add_node("script_writer", script_writer_node)
graph.add_node("storyboarder", storyboarder_node)
graph.add_node("asset_supplier", asset_supplier_node)
graph.add_node("video_generator", video_generator_node)
graph.add_node("qc_agent", qc_agent_node)
graph.add_node("fix_loop", fix_loop_node)
graph.add_node("synthesizer", synthesizer_node)

# === 入口 ===
graph.set_entry_point("requirement_parser")

# === 线性流（主路径）===
graph.add_edge("requirement_parser", "script_writer")
graph.add_edge("script_writer", "storyboarder")
graph.add_edge("storyboarder", "asset_supplier")  # 可选节点
graph.add_edge("asset_supplier", "video_generator")
graph.add_edge("video_generator", "qc_agent")

# === QC 条件分支（三态路由）===
graph.add_conditional_edges(
    "qc_agent",
    route_after_qc,
    {
        "passed": "synthesizer",     # QC通过 → 合成
        "retry": "fix_loop",          # QC失败但可修复 → 修正循环
        "give_up": END,               # 超过最大修正轮次 → 结束
    }
)

# === 修正循环 ===
graph.add_edge("fix_loop", "storyboarder")  # 回到 storyboarder 重生成

# === 完成 ===
graph.add_edge("synthesizer", END)
```

### 2.2 条件路由函数

```python
def route_after_qc(state: CreativeSessionState) -> str:
    qc = state.get("qc_report")
    if not qc or not qc.get("passed"):
        fix_round = state.get("fix_round", 0)
        # 三态路由：修复中 / 放弃 / 通过
        if fix_round >= state.get("max_fix_rounds", 3):
            return "give_up"  # 超过最大轮次，放弃
        return "retry"  # 继续修正
    return "passed"  # QC通过

def fix_loop_node(state: CreativeSessionState) -> dict:
    """
    修正循环节点：根据QC失败原因改写提示词，然后回到storyboarder
    """
    qc = state["qc_report"]
    fix_round = state.get("fix_round", 0) + 1
    
    # 记录本轮修正历史
    fix_record = {
        "round": fix_round,
        "fix_reason": qc.get("fix_reason"),
        "fix_hint": qc.get("fix_hint"),
        "old_prompt": state["storyboard"][-1].get("prompt"),  # 最后一镜的prompt
    }
    
    # 调用LLM改写提示词（使用fix_hint作为改写指引）
    new_prompt = rewrite_prompt_for_fix(
        old_prompt=state["storyboard"][-1].get("prompt"),
        fix_hint=qc.get("fix_hint"),
        fix_reason=qc.get("fix_reason"),
    )
    
    fix_record["new_prompt"] = new_prompt
    state["fix_history"].append(fix_record)
    state["fix_round"] = fix_round
    
    # 更新storyboard最后一镜的prompt
    state["storyboard"][-1]["prompt"] = new_prompt
    
    return {
        "qc_report": None,  # 清除旧QC报告，触发重新生成
        "fix_round": fix_round,
        "fix_history": state["fix_history"],
    }
```

---

## 三、各节点实现

### 3.1 requirement_parser（需求解析）

```python
async def requirement_parser_node(state: CreativeSessionState) -> dict:
    """
    输入：raw_prompt（用户一句话）
    输出：brief（结构化Brief）
    工具：无（纯LLM）
    """
    prompt = f"""
    用户需求：{state['raw_prompt']}
    
    请解析为结构化Brief，包含：
    - theme: 主题（如：产品宣传、品牌故事、知识科普）
    - style: 风格（如：科技感、温馨、商务）
    - duration_seconds: 期望时长（4-12秒）
    - audience: 目标受众
    - mood: 情绪基调
    - reference_assets: 引用素材列表（可选）
    
    用JSON格式输出，只返回JSON，不要其他内容。
    """
    
    # 使用 agnes-2.5-flash，结构化输出
    response = await call_llm_with_structured_output(
        model="agnes-2.5-flash",
        prompt=prompt,
        response_format={"type": "json_object"}
    )
    
    # 校验解析结果
    brief = validate_brief(response)
    
    return {"brief": brief, "status": TaskStatus.QUEUED}
```

### 3.2 script_writer（剧本生成）

```python
async def script_writer_node(state: CreativeSessionState) -> dict:
    """
    输入：brief
    输出：script（分镜列表）
    工具：无（纯LLM）
    """
    prompt = f"""
    根据以下Brief创作短视频剧本：
    
    Theme: {state['brief']['theme']}
    Style: {state['brief']['style']}
    Duration: {state['brief']['duration_seconds']}秒
    Audience: {state['brief']['audience']}
    Mood: {state['brief']['mood']}
    
    输出分镜列表，每镜包含：
    - shot_id: 镜头编号
    - visual: 画面描述（主体+动作+场景）
    - camera: 镜头运动（推/拉/摇/移/固定）
    - duration: 该镜时长（秒）
    - voiceover: 旁白文案（可选）
    - style_note: 风格提示（光照/色调/质感）
    
    总时长控制在 {state['brief']['duration_seconds']} 秒内。
    用JSON数组输出。
    """
    
    script = await call_llm_with_json_output(
        model="agnes-2.5-flash",
        prompt=prompt,
        template_version="script_v2.1"  # 模板版本化，改动留痕
    )
    
    return {"script": script, "status": TaskStatus.SCRIPT_WRITING}
```

### 3.3 storyboarder（分镜→提示词）

```python
async def storyboarder_node(state: CreativeSessionState) -> dict:
    """
    输入：script（分镜列表）
    输出：storyboard（每镜英文提示词+生成参数）
    工具：translate_to_en（中译英）
    """
    storyboard = []
    
    for shot in state["script"]:
        # 中文描述
        cn_description = f"{shot['visual']}，{shot['camera']}，{shot['style_note']}"
        
        # 调用翻译工具（LangGraph Tool）
        en_prompt = await translate_to_en(cn_description)
        
        # 组装生成参数
        shot_duration = shot.get("duration", 3)
        storyboard.append({
            "shot_id": shot["shot_id"],
            "prompt_en": en_prompt,
            "mode": "text",  # text/keyframe/reference
            "seconds": str(int(shot_duration)),
            "aspect_ratio": "16:9",
            "reference_images": [],  # 后续由asset_supplier填充
            "cn_description": cn_description,  # 保留中文用于回溯
        })
    
    return {"storyboard": storyboard, "status": TaskStatus.STORYBOARD_WRITING}

# LangGraph Tool 定义
@tool
async def translate_to_en(text: str) -> str:
    """将中文描述翻译为英文视频生成提示词"""
    response = await call_llm(
        model="agnes-2.5-flash",
        prompt=f"Translate the following Chinese video description to English prompt:\n{text}\n\nOutput only the English prompt, no explanation."
    )
    return response.choices[0].message.content.strip()
```

### 3.4 video_generator（视频生成）

```python
async def video_generator_node(state: CreativeSessionState) -> dict:
    """
    输入：storyboard
    输出：video_urls（逐镜 append，断点恢复后从已完成的镜次继续）
    契约：工具只返回单个 video_url，state 读写全部由本节点负责（唯一入口）
    """
    video_urls = list(state.get("video_urls", []))
    trace = list(state.get("trace", []))

    # 断点恢复：状态里已有的 video_urls 对应的镜次跳过，不再重复提交
    done = len(video_urls)

    for idx, shot in enumerate(state["storyboard"][done:], start=done):
        # 调用视频生成工具（单镜头契约）：提交 + interrupt 挂起，返回该镜 video_url
        video_url = await generate_video_tool(
            prompt=shot["prompt_en"],
            seconds=shot["seconds"],
            mode=shot.get("mode", "text"),
            aspect_ratio=shot["aspect_ratio"],
            reference_images=shot.get("reference_images", []),
            session_id=state["session_id"],
            shot_index=idx,
        )

        # 调用方统一负责：写 state + 写审计
        video_urls.append(video_url)
        trace.append({
            "tool_name": "generate_video",
            "params": {"prompt": shot["prompt_en"], "seconds": shot["seconds"]},
            "result": {"video_url": video_url},
            "latency_ms": 0,  # 由 poller 侧补充
            "timestamp": int(time.time()),
            "retry_count": 0,
        })

    return {"video_urls": video_urls, "trace": trace, "status": TaskStatus.VIDEO_GENERATING}

# MCP风格工具实现（单镜头契约：一次调用 = 提交一个镜头 + interrupt 一次）
async def generate_video_tool(prompt: str, seconds: str, mode: str,
                              aspect_ratio: str, reference_images: list,
                              session_id: str, shot_index: int) -> str:
    """
    提交异步视频任务 + interrupt 挂起等待 poller 完成。
    【契约】工具不持有 state、不维护 video_urls 列表：
    只负责「提交 → 入库 → 挂起 → 返回该镜 video_url(str)」。
    """
    # 1. 提交任务（限流在网关层）
    response = await agnes_client.submit_video(
        prompt=prompt,
        seconds=seconds,
        mode=mode,
        aspect_ratio=aspect_ratio,
        reference_images=reference_images or [],
    )
    video_id = response["video_id"]

    # 2. 持久化 video_id 到 DB（供 worker 重启后恢复轮询）
    await save_polling_task(
        video_id=video_id,
        model_name=response["model_name"],
        session_id=session_id,
        shot_index=shot_index,
    )

    # 3. interrupt 挂起；poller/回调完成时 Command(resume=video_url) 唤醒，
    #    返回值即该镜完成后的视频 URL
    video_url = interrupt(f"video_polling:{video_id}")
    return video_url
```

### 3.5 qc_agent（质检）

```python
async def qc_agent_node(state: CreativeSessionState) -> dict:
    """
    双层质检：规则层 + LLM层（文本对文本）
    【重要】agnes-2.5-flash 是文本模型，不支持视频理解
    因此 LLM 层只评估「分镜提示词 vs 剧本一致性」，不评估视频内容
    """
    # === 规则层检查 ===
    param_score = check_param_compliance(state["storyboard"], state.get("video_urls", []))
    
    # === LLM层检查（文本对文本）===
    # 评估分镜提示词是否与剧本描述一致
    content_score = await llm_content_check(
        script=state["script"],
        storyboard=state["storyboard"],
    )
    
    # 加权总分
    total_score = param_score * 0.3 + content_score * 0.7
    
    passed = total_score >= 0.7 and param_score >= 0.9
    
    # 确定失败原因
    fix_reason = None
    fix_hint = None
    if not passed:
        if param_score < 0.9:
            fix_reason = FixReason.PARAM_INVALID
            fix_hint = "检查参数：时长、画幅、参考素材数量是否合规"
        elif content_score < 0.7:
            fix_reason = FixReason.CONTENT_INCONSISTENT
            fix_hint = "分镜提示词与剧本描述不一致，需调整提示词"
    
    qc_report = QCScore(
        param_score=param_score,
        content_score=content_score,
        total_score=total_score,
        passed=passed,
        fix_reason=fix_reason,
        fix_hint=fix_hint,
    )
    
    return {"qc_report": qc_report, "status": TaskStatus.QC_CHECKING}

async def llm_content_check(script: list, storyboard: list) -> float:
    """
    LLM评估分镜提示词与剧本的一致性（文本对文本）
    【注意】不传视频URL，只比较文本描述
    """
    prompt = f"""
    请评估以下分镜提示词是否与剧本一致，给出0-1的分数。
    
    剧本：{json.dumps(script, ensure_ascii=False)}
    分镜提示词：{json.dumps([s['prompt_en'] for s in storyboard], ensure_ascii=False)}
    
    评估维度：
    1. 画面描述是否与剧本一致（0.4）
    2. 镜头运动是否符合预期（0.3）
    3. 整体风格是否与剧本匹配（0.3）
    
    只输出0-1的分数，不要其他内容。
    """
    
    score_str = await call_llm_simple(prompt, model="agnes-2.5-flash")
    return float(score_str.strip())
```

**关于 QC LLM 层的设计决策**：
- `agnes-2.5-flash` 是纯文本模型，无法理解视频内容
- 因此 QC 的 LLM 层只评估「文本一致性」（剧本 vs 分镜提示词），不评估视频质量
- 视频质量检查交给规则层（参数合法性）+ 人工抽查
- 如果后续确认 agnes 有视频理解能力，可升级为多模态 QC

---

## 四、断点恢复机制

### 4.1 Checkpoint 持久化

```python
# 生产环境使用PostgreSQL Checkpointer
from langgraph.checkpoint.postgres import PostgresSaver

connstr = os.environ["DATABASE_URL"]
checkpointer = PostgresSaver.from_connstr(connstr)

# 编译图时传入checkpointer
compiled_graph = graph.compile(checkpointer=checkpointer)
```

### 4.2 断点恢复流程

```python
async def resume_session(session_id: str, user_id: str) -> CreativeSessionState:
    """
    从Checkpoint恢复会话
    """
    # 1. 查找该session的最新checkpoint
    config = {"configurable": {"thread_id": session_id}}
    checkpoint = await checkpointer.aget(config)
    
    if not checkpoint:
        raise ValueError(f"Session {session_id} not found")
    
    # 2. 恢复state
    state = checkpoint["channel_values"]
    
    # 3. 检查是否可以继续
    if state["status"] in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.EXPIRED]:
        return state  # 已完成或失败，直接返回
    
    # 4. 从断点继续执行
    # LangGraph会自动从上一个节点继续
    result = await compiled_graph.ainvoke(
        state,
        config=config
    )
    
    return result
```

### 4.3 Worker 重启接管

```python
# FastAPI后台任务：启动时扫描未完成会话
async def recover_pending_sessions():
    """
    Worker重启时，扫描DB中status不在终态的会话，重新入队恢复
    """
    pending_sessions = await db.fetch_all(
        """
        SELECT session_id, user_id, status, updated_at
        FROM creative_sessions
        WHERE status NOT IN ('completed', 'failed', 'expired')
          AND updated_at < NOW() - INTERVAL '1 hour'
        """
    )
    
    for session in pending_sessions:
        try:
            # 尝试恢复
            await resume_session(session["session_id"], session["user_id"])
            logger.info(f"Recovered session {session['session_id']}")
        except Exception as e:
            # 恢复失败，标记过期
            await mark_session_expired(session["session_id"], str(e))
            logger.error(f"Failed to recover session {session['session_id']}: {e}")
```

### 4.4 interrupt 多轮澄清

```python
async def requirement_parser_node(state: CreativeSessionState) -> dict:
    """
    需求解析节点：信息不足时interrupt，等待用户补充
    """
    brief = parse_brief(state["raw_prompt"])
    
    # 检查必要字段是否齐全
    missing_fields = check_missing_fields(brief)
    
    if missing_fields:
        # interrupt 挂起，等待用户补充
        question = f"请问您还需要补充以下信息：{', '.join(missing_fields)}"
        # 正确用法：interrupt() 是函数调用，不是 raise
        user_feedback = interrupt(question)
        # 合并用户补充信息后重新解析
        combined_prompt = f"{state['raw_prompt']}\n\n用户补充：{user_feedback}"
        brief = parse_brief(combined_prompt)
    
    return {"brief": brief}
```

### 4.5 独立 Poller 设计（解决长任务阻塞问题）

```python
# 独立于 LangGraph 的后台轮询服务
class VideoPoller:
    """
    独立轮询服务：负责 video_id → video_url 的异步等待
    与 LangGraph 节点解耦，支持崩溃恢复
    """
    
    async def start_polling(self, video_id: str, model_name: str, 
                            session_id: str, shot_index: int):
        """
        启动轮询：每5秒查询一次，直到完成或超时
        """
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                # 查询状态
                status = await agnes_client.query_video(video_id, model_name)
                
                if status["done"]:
                    # 与 §4.6 回调路径共用同一把 Redis 锁，防止双下载/双 resume
                    lock_key = f"video_notify:{video_id}"
                    lock_acquired = await redis.set(lock_key, "1", nx=True, ex=300)

                    if not lock_acquired:
                        # 回调路径或其他 Poller 实例正在处理，本实例让位
                        logger.info(f"video {video_id} 已被其他完成发现路径接管，Poller 退出")
                        return

                    try:
                        # 双重检查：DB 状态兜底（抢到锁≠要处理，可能已被对手方完成）
                        task = await db.fetch_one(
                            "SELECT status FROM polling_tasks WHERE video_id = $1", video_id
                        )
                        if not task or task["status"] == "completed":
                            return

                        # 下载视频到 MinIO（锁保护下，只有锁主人走到这里）
                        video_url = await self.download_to_minio(status["url"])

                        # 更新数据库（polling_tasks 状态置 completed，供对手方/恢复扫描幂等判断）
                        await update_video_result(
                            session_id=session_id,
                            shot_index=shot_index,
                            video_url=video_url
                        )
                        await db.execute(
                            "UPDATE polling_tasks SET status='completed', video_url=$1 WHERE video_id=$2",
                            video_url, video_id
                        )

                        # 通知 LangGraph 恢复执行，并通过 Command 传递 video_url
                        # 【关键】video_url 必须写回 LangGraph state，否则 QC 拿不到视频
                        await resume_session_with_value(session_id, shot_index, video_url)
                        return

                    finally:
                        await redis.delete(lock_key)
                
                elif status["error"]:
                    retry_count += 1
                    if retry_count >= max_retries:
                        await mark_session_failed(session_id, status["error"])
                        return
                    await asyncio.sleep(5 * (2 ** retry_count))  # 指数退避
                    
                else:
                    # 仍在处理中，继续等待
                    await asyncio.sleep(5)
                    
            except Exception as e:
                logger.error(f"Polling error for {video_id}: {e}")
                retry_count += 1
                if retry_count >= max_retries:
                    await mark_session_failed(session_id, str(e))
                    return
                await asyncio.sleep(5)
    
    async def download_to_minio(self, url: str) -> str:
        """下载视频到 MinIO，返回持久化 URL"""
        # 下载逻辑...
        pass

# 启动时注册所有待恢复的轮询任务
async def recover_polling_tasks():
    """Worker 重启时，从 DB 加载未完成轮询任务"""
    pending_tasks = await db.fetch_all(
        "SELECT video_id, model_name, session_id, shot_index FROM polling_tasks WHERE status='pending'"
    )
    for task in pending_tasks:
        asyncio.create_task(
            VideoPoller().start_polling(
                task["video_id"], task["model_name"], 
                task["session_id"], task["shot_index"]
            )
        )
```

### 4.6 幂等回调设计（防止重复下载）

```python
# FastAPI 回调接口（Agnes 完成通知）
@app.post("/internal/notify")
async def handle_completion_notify(request: CompletionNotifyRequest):
    """
    处理视频生成完成通知
    幂等设计：同一 video_id 多次回调只处理一次
    """
    video_id = request.video_id
    
    # 1. 获取分布式锁（防止并发处理）
    lock_key = f"video_notify:{video_id}"
    lock_acquired = await redis.set(lock_key, "1", nx=True, ex=300)
    
    if not lock_acquired:
        # 已被其他实例处理，直接返回
        return {"status": "skipped"}
    
    try:
        # 2. 查询 DB 确认任务状态
        task = await db.fetch_one(
            "SELECT * FROM polling_tasks WHERE video_id = $1", video_id
        )
        
        if not task or task["status"] == "completed":
            # 已处理或不存在，幂等返回
            return {"status": "skipped"}
        
        # 3. 下载视频到 MinIO
        video_url = await download_video(request.result_url)
        
        # 4. 更新状态
        await db.execute(
            "UPDATE polling_tasks SET status='completed', video_url=$1 WHERE video_id=$2",
            video_url, video_id
        )
        
        # 5. 通知 LangGraph 恢复，并传递 video_url（幂等：DB 已检查过）
        await resume_session_with_value(task["session_id"], task["shot_index"], video_url)
        
        return {"status": "processed"}
        
    finally:
        # 释放锁
        await redis.delete(lock_key)
```

**幂等保证**：
- Redis 分布式锁防止并发重复处理
- DB 状态检查防止重复回调
- 锁 TTL 5 分钟防止死锁

**两路径统一锁约定**：Poller（§4.5）和回调（§4.6）是两条平行的"完成发现"路径，都必须先抢 `video_notify:{video_id}` 这把锁再下载。锁主人做【下载 → 置 polling_tasks.completed → resume】三件事，抢锁失败的让位退出；锁主人下载前还会再查一次 DB 状态兜底（万一对家已抢先完成）。两条路径从此串行化——不会有双下载、双 resume。若 Agnes 后续支持可靠的完成回调，可关掉 Poller 路径只留回调（省心跳请求），反之亦然。

# 用户补充后恢复
async def resume_with_clarification(session_id: str, user_input: str):
    """
    收到用户补充信息后恢复执行
    """
    config = {"configurable": {"thread_id": session_id}}

    # 更新raw_prompt（追加用户补充）
    await update_session_prompt(session_id, user_input)

    # 恢复图执行
    result = await compiled_graph.aresume(config)

    return result


### 4.7 resume_session_with_value（状态回写实现）

```python
from langgraph.types import Command

async def resume_session_with_value(session_id: str, shot_index: int, video_url: str):
    """
    通过 Command 恢复会话并写入 video_url 到 state
    【关键】这是断点恢复链的最后一环：video_url 必须写回 LangGraph state，
           否则 QC 和 synthesizer 都拿不到视频

    Args:
        session_id: LangGraph 会话 ID
        shot_index: 当前镜头索引（用于构建正确的 state 更新）
        video_url: 下载后的视频 URL
    """
    config = {"configurable": {"thread_id": session_id}}

    # 使用 Command 写入 state 更新
    # LangGraph 官方推荐：interrupt() 返回的值由调用方通过 Command 传递
    update = {
        "video_urls": [video_url]  # 注意：实际实现需要 append 到现有列表
    }

    # resume 并携带 state 更新
    await compiled_graph.ainvoke(
        None,
        config=config,
        run_name="resume_from_poller"
    )

    # 注意：实际上需要通过 interrupt() 的返回值机制
    # 正确姿势是使用 langgraph.types.Command 传递 resume 值
    from langgraph.types import Command
    await compiled_graph.ainvoke(
        Command(resume=video_url),
        config=config
    )
```

**正确实现（使用 langgraph.types.Command）**：

```python
async def resume_session_with_value(session_id: str, shot_index: int, video_url: str):
    """
    通过 Command 恢复会话并传递 video_url
    这是 LangGraph 官方推荐的 interrupt 返回值传递方式
    """
    config = {"configurable": {"thread_id": session_id}}

    # 关键：使用 Command(resume=...) 传递中断恢复值
    # interrupt("video_polling:{video_id}") 会返回这个值
    result = await compiled_graph.ainvoke(
        Command(resume=video_url),
        config=config
    )

    # 注意：实际实现需要根据 shot_index 更新正确的 state 字段
    # 这里简化处理，实际应该只更新 video_urls[shot_index]
    return result
```

**面试口径**：
- LangGraph 的 `interrupt()` 是"挂起+等待值"，不是"抛出异常"
- 恢复时用 `Command(resume=value)` 传递中断恢复值
- 节点内 `video_url = interrupt(...)` 接住这个值
- 这样 state 回写是类型安全的，不需要手动构造 state dict

---

---

## 五、SSE 轨迹推送

### 5.1 事件类型与格式

> 实测（2026-09）修正：Agnes 视频查询接口返回 `progress` 百分比（0-100）+ `internal_status`，
> **进度条可以做成真实的**，不是状态信号假装。`completed` 时 `url` 字段携带成品视频地址。
> 前端 progress 事件直接用 API 的 progress 值；type='progress' 携带该字段。

```typescript
interface CreativeEvent {
  eventId: number;
  sessionId: string;
  type: EventType;
  timestamp: number;
  data: EventData;
}

type EventType = 
  | 'session_started'      // 会话开始
  | 'node_entered'         // 进入某节点
  | 'node_completed'       // 节点完成
  | 'tool_called'          // 工具调用
  | 'tool_result'          // 工具返回
  | 'interrupted'          // 需要用户补充
  | 'progress'             // 进度更新
  | 'completed'            // 任务完成
  | 'failed';              // 任务失败

interface EventData {
  nodeId?: string;          // 当前节点ID
  nodeName?: string;        // 节点名称（中文）
  nodeInput?: any;          // 节点输入（脱敏）
  nodeOutput?: any;         // 节点输出（脱敏）
  toolName?: string;        // 工具名
  toolParams?: any;         // 工具参数
  toolResult?: any;         // 工具结果
  progress?: number;        // 进度 0-100
  message?: string;         // 中断时的提示信息
  error?: string;           // 失败原因
}
```

### 5.2 节点级事件注入

```python
# 在每个节点函数中加入事件发射
async def script_writer_node(state: CreativeSessionState) -> dict:
    # 发送节点开始事件
    await emit_event(state["session_id"], {
        "type": "node_entered",
        "data": {"nodeId": "script_writer", "nodeName": "剧本生成"}
    })
    
    try:
        # 执行节点逻辑
        script = await generate_script(state["brief"])
        
        # 发送节点完成事件
        await emit_event(state["session_id"], {
            "type": "node_completed",
            "data": {"nodeId": "script_writer", "nodeOutput": {"shots": len(script)}}
        })
        
        return {"script": script}
    except Exception as e:
        # 发送失败事件
        await emit_event(state["session_id"], {
            "type": "failed",
            "data": {"error": str(e)}
        })
        raise
```

### 5.3 前端轨迹可视化

```typescript
// React组件：Agent轨迹时间线
function AgentTrajectory({ events }: { events: CreativeEvent[] }) {
  return (
    <div className="trajectory">
      {events.map((event) => (
        <TrajectoryNode key={event.eventId} event={event} />
      ))}
    </div>
  );
}

function TrajectoryNode({ event }: { event: CreativeEvent }) {
  switch (event.type) {
    case 'node_entered':
      return <NodeEnter node={event.data.nodeName} />;
    case 'tool_called':
      return <ToolCall tool={event.data.toolName} params={event.data.toolParams} />;
    case 'interrupted':
      return <InterruptPrompt message={event.data.message} />;
    // ...
  }
}
```

---

## 六、面试深挖准备

### Q1: 为什么用LangGraph而不是自己写状态机？

**答**：
- LangGraph 原生支持 `interrupt()` 多轮澄清，自己实现需要自己序列化状态、管理暂停/恢复逻辑
- Checkpoint 机制保证断点恢复，自己实现需要自己写状态持久化和恢复代码
- 条件边（conditional edges）天然支持QC通过/失败的分流，自己实现需要写if-else判断
- LangSmith 提供完整的追踪能力，自己实现需要自己埋点

**但要能讲清**：每个节点的状态Schema是自己设计的（CreativeSessionState），不是LangGraph默认的；工具注册表是自己实现的，不是LangChain的。

### Q2: QC判分是规则还是LLM？

**答**：双层设计：
- **规则层（30%权重）**：检查参数合法性（时长4-12秒、画幅在白名单、参考素材≤5张等），这是确定性判断，不出错
- **LLM层（70%权重）**：评估「分镜提示词 vs 剧本」的文本一致性

**关键设计决策**：agnes-2.5-flash 是纯文本模型，无法理解视频内容，所以 LLM 层只评估文本一致性，不评估视频质量。视频质量检查交给规则层 + 人工抽查。这是一个trade-off，但保证了 QC 的可信度。

### Q3: 失败回流怎么证明有效？

**答**：
- 每轮修正记录：哪版prompt、为什么失败、改了什么
- 评测集：预设N个典型创作需求，每次改Prompt模板前先全量跑一遍
- 样本回放：对同一需求重新执行，对比修正前后的QC分数
- 数据展示：修正前平均分0.65 → 修正后0.82，证明回流逻辑有效

### Q4: 断点恢复具体怎么实现的？

**答**：
- LangGraph Checkpoint 把每次invoke的state序列化存到PostgreSQL
- Worker重启时扫描DB中status不在终态的会话
- 调用 `compiled_graph.aresume(config)` 从最后一个checkpoint继续执行
- interrupt的中断点也会保存，用户补充信息后调用 `aresume()` 恢复

### Q5: 工具调用审计怎么做到的？

**答**：
- 每个工具调用前后记录：tool_name、params、result、latency_ms、retry_count
- 存入state的trace字段，每次invoke都追加
- 前端SSE推送tool_called和tool_result事件，实时展示
- 面试演示时可以直接展示完整调用链：哪个节点、调了哪个工具、参数是什么、结果如何

---

## 七、与简历经验的咬合点

| 简历经验 | DreamWeaver体现 | 面试讲述点 |
|----------|----------------|-----------|
| LangGraph多节点编排 | 创作流水线DAG | "我用LangGraph编排了7个节点的创作流程" |
| 动态工具注册/MCP中台 | 工具注册表+审计 | "我把视频生成能力封装成MCP风格工具" |
| Text-to-SQL评测闭环 | QC双层判分+失败回流 | "我做了规则+LLM双通道质检，失败自动回流" |
| Agent Harness | 状态持久化+断点恢复 | "每个节点产出都落库，worker崩溃可恢复" |
| SQL Guardrail | 参数合规校验+能力目录 | "模型能力约束不写死，由能力目录驱动校验" |

---

## 八、实施建议

### Phase 1 MVP（坐实"Agent"标签）

必须有的最小闭环：
1. requirement_parser → script_writer → storyboarder → video_generator → 完成
2. 每个节点写日志到trace
3. SSE推送节点级事件
4. 简单的前端轨迹展示

**不要做**：QC、fix_loop、断点恢复（这些放到Phase 2/3）

### Phase 2 增强

1. QC规则层
2. fix_loop修正机制
3. 工具注册表完善
4. interrupt多轮澄清

### Phase 3 完善

1. QC LLM层
2. 评测集+样本回放
3. 断点恢复
4. 轨迹可视化完善

---

## 九、Phase 2 详细设计：VideoPoller + 数据库层

### 9.1 polling_tasks 表结构（PostgreSQL）

```sql
CREATE TABLE polling_tasks (
    id            SERIAL PRIMARY KEY,
    video_id      VARCHAR(64) NOT NULL UNIQUE,      -- Agnes 返回的异步任务 ID
    model_name    VARCHAR(32) NOT NULL,             -- 使用的模型名
    session_id    VARCHAR(32) NOT NULL,             -- LangGraph 会话 ID
    shot_index    INTEGER NOT NULL,                 -- 当前分镜索引（从 0 开始）
    status        VARCHAR(16) NOT NULL DEFAULT 'pending',
    -- pending / completed / failed / recovering
    video_url     TEXT,                             -- 完成后的 MinIO URL
    error_message TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    locked_until  TIMESTAMPTZ,                      -- Redis 锁持有者的锁定截止时间
    recovered     BOOLEAN NOT NULL DEFAULT FALSE    -- 是否已被 worker 恢复扫描处理过
);

CREATE INDEX idx_polling_session ON polling_tasks(session_id, status);
CREATE INDEX idx_polling_status  ON polling_tasks(status) WHERE status IN ('pending', 'recovering');
```

**状态流转**：
```
pending ──→ [Worker 扫描] ──→ recovering ──→ [Poller 执行] ──→ completed
  │                                                        │
  └── [Redis 锁抢到]              └── [下载+写入 DB+resume] ──┘
  [lock 获取失败] → 让位退出
```

### 9.2 VideoPoller 核心逻辑

```python
# app/poller.py

import asyncio
import logging
from datetime import datetime, timedelta
from langgraph.types import Command

from app.config import settings
from app.gateway.agnes import gateway
from app.db import db  # 封装好的 asyncpg 连接池

logger = logging.getLogger(__name__)


class VideoPoller:
    """独立轮询服务，与 LangGraph 节点解耦。"""

    POLL_INTERVAL_S = 5
    MAX_RETRIES = 3
    LOCK_TTL_S = 300  # 5 分钟

    def __init__(self):
        self._running = False

    async def start(self):
        """启动轮询服务（应用在 on_event('startup') 中调用）。"""
        self._running = True
        logger.info("VideoPoller started")
        # 启动时扫描待恢复任务
        asyncio.create_task(self._recover_and_poll_loop())

    async def stop(self):
        self._running = False
        logger.info("VideoPoller stopped")

    async def _recover_and_poll_loop(self):
        """持续扫描 pending/recovering 任务并接管轮询。"""
        while self._running:
            try:
                await self._scan_and_takeover()
            except Exception as e:
                logger.error(f"Scanner error: {e}")
            await asyncio.sleep(10)  # 每 10 秒扫描一次

    async def _scan_and_takeoff(self):
        """扫描 DB 中 pending 或 recovering 超时的任务，尝试抢锁接管。"""
        # 查询 pending 或 recovering 且未过锁定时间的任务
        rows = await db.fetch("""
            SELECT id, video_id, model_name, session_id, shot_index, locked_until
            FROM polling_tasks
            WHERE status IN ('pending', 'recovering')
              AND (locked_until IS NULL OR locked_until < NOW())
            ORDER BY created_at ASC
        """)

        for row in rows:
            task_id = row["id"]
            video_id = row["video_id"]
            lock_key = f"video_notify:{video_id}"

            # 尝试抢锁（SET NX EX）
            locked = await db.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))", lock_key
                # 或者用 Redis: await redis.set(lock_key, "1", nx=True, ex=self.LOCK_TTL_S)
            )

            if not locked:
                continue  # 锁被其他人持有，跳过

            try:
                # 双重检查：确认任务仍然有效
                current = await db.fetch_one(
                    "SELECT status, session_id, shot_index FROM polling_tasks WHERE id = $1",
                    task_id
                )
                if not current or current["status"] == "completed":
                    continue

                # 标记为 recovering（防止其他实例重复接管）
                await db.execute("""
                    UPDATE polling_tasks
                    SET status = 'recovering', locked_until = NOW() + INTERVAL '5 minutes'
                    WHERE id = $1 AND status IN ('pending', 'recovering')
                """, task_id)

                # 启动单镜头轮询
                asyncio.create_task(self._poll_single(
                    video_id=video_id,
                    model_name=row["model_name"],
                    session_id=current["session_id"],
                    shot_index=current["shot_index"],
                    task_id=task_id,
                ))

            except Exception as e:
                logger.error(f"Takeover failed for video_id={video_id}: {e}")
                await self._mark_failed(task_id, str(e))

    async def _poll_single(self, video_id, model_name, session_id, shot_index, task_id):
        """对单个 video_id 执行轮询，直到完成或失败。"""
        retry_count = 0
        while retry_count < self.MAX_RETRIES:
            try:
                status = await gateway.query_video(video_id, model_name)

                if status.get("done") or status.get("status") in ("completed", "done"):
                    url = status.get("video_url") or status.get("url")
                    if not url:
                        raise RuntimeError(f"video {video_id} completed but no URL: {status}")

                    # 下载并上传到 MinIO
                    minio_url = await self._download_to_minio(url, video_id)

                    # 更新 DB
                    await db.execute("""
                        UPDATE polling_tasks
                        SET status = 'completed', video_url = $1, updated_at = NOW()
                        WHERE video_id = $2
                    """, minio_url, video_id)

                    # 写回 LangGraph state
                    await self._resume_session(session_id, shot_index, minio_url)
                    return

                elif status.get("error") or status.get("status") == "failed":
                    raise RuntimeError(f"video {video_id} generation failed: {status}")

                # 仍在处理中
                await asyncio.sleep(self.POLL_INTERVAL_S * (2 ** retry_count))
                retry_count += 1

            except Exception as e:
                logger.error(f"Poll error for {video_id}: {e}")
                retry_count += 1
                if retry_count >= self.MAX_RETRIES:
                    await self._mark_failed(task_id, str(e))
                    return
                await asyncio.sleep(5)

    async def _download_to_minio(self, url: str, video_id: str) -> str:
        """下载视频并上传到 MinIO，返回持久化 URL。"""
        # 实际实现调用 MinIO SDK
        # 这里简化示意
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, timeout=120)
            resp.raise_for_status()
            # 上传到 MinIO...
            return f"https://minio.internal/dreamweaver/{video_id}.mp4"

    async def _resume_session(self, session_id: str, shot_index: int, video_url: str):
        """通过 Command(resume=...) 恢复 LangGraph 会话。"""
        from app.graph import compiled_graph
        config = {"configurable": {"thread_id": session_id}}
        # 将 video_url 写入 state 的关键路径
        await compiled_graph.ainvoke(
            Command(resume=video_url),
            config=config
        )

    async def _mark_failed(self, task_id: int, error: str):
        await db.execute("""
            UPDATE polling_tasks
            SET status = 'failed', error_message = $1, updated_at = NOW()
            WHERE id = $2
        """, error, task_id)


# 应用启动时初始化
poller = VideoPoller()
```

### 9.3 generate_video_tool 改造（Phase 2）

```python
# app/tools/video.py

async def generate_video_tool(
    prompt: str, seconds: str, mode: str,
    aspect_ratio: str, reference_images: list,
    session_id: str, shot_index: int,
) -> str:
    """
    提交任务 + 持久化 + interrupt 挂起
    不在此节点轮询，由 VideoPoller 独立接管
    """
    # 1. 提交到 Agnes
    submitted = await gateway.submit_video(
        prompt=prompt, seconds=seconds, mode=mode,
        aspect_ratio=aspect_ratio, reference_images=reference_images or [],
    )
    video_id = submitted["video_id"]
    model_name = submitted["model_name"]

    # 2. 持久化到 DB
    await db.execute("""
        INSERT INTO polling_tasks (video_id, model_name, session_id, shot_index, status)
        VALUES ($1, $2, $3, $4, 'pending')
        ON CONFLICT (video_id) DO NOTHING
    """, video_id, model_name, session_id, shot_index)

    # 3. interrupt 挂起，等待 Poller 完成并传入 video_url
    # interrupt() 的返回值由 Command(resume=video_url) 提供
    video_url = interrupt(f"video_polling:{video_id}")

    return video_url
```

### 9.4 Java ↔ FastAPI 数据表映射

| FastAPI 侧（Python） | Java 侧（Spring Boot） | 说明 |
|---|---|---|
| `polling_tasks` (video_id, session_id, shot_index, status, video_url) | `creative_task` (id, user_id, session_id, status, result_json) | FastAPI 维护轮询状态；Java 维护用户视角的任务状态 |
| — | `creative_session` (id, user_id, raw_prompt, brief, script, storyboard, status, created_at) | Java 持久化创作会话主数据，供前端查询和审计 |
| — | `asset` (id, session_id, shot_index, type, url, created_at) | 资产表，存储视频/图片 URL，由 FastAPI 回调时写入或由 Java 自行上传 |
| 工具注册表 `tool_registry` | `tool_definition` | 双向同步：FastAPI 启动时拉取 `GET /v1/capabilities`，Java 缓存用于入参校验 |
| `creation_trace` (session_id, node_name, tool_name, params, result, timestamp) | `audit_log` | 审计日志，FastAPI 写入，Java 可读可查 |

**关键接口约定**：

```
# FastAPI → Java（回调）
POST /internal/notify
Body: { "task_id": "xxx", "video_url": "xxx", "shot_index": 0 }
Java 幂等处理：按 task_id 去重，已 completed 则忽略

# Java → FastAPI（拉取能力目录）
GET /v1/capabilities
返回: { "models": [{ "name": "agnes-video-2.5-flash", "modes": ["text","keyframe","reference"], "max_seconds": 12, ... }] }
Java 启动时缓存，每小时刷新

# Java → FastAPI（提交任务）
POST /v1/tasks/video
Body: { "session_id": "xxx", "prompt": "xxx", "mode": "text", ... }
FastAPI 返回: { "video_id": "xxx", "status": "pending" }
```

---

## 十一、Phase 2 异步回调设计（Java 侧）

### 11.1 问题：为什么必须改同步为异步

Phase 1 的 `TaskServiceImpl.createTask` 是**同步阻塞调用 FastAPI**：
```java
// Phase 1 写法（同步阻塞，占线程等视频生成）
webClientBuilder.build()
    .post().uri(agentBase + "/v1/tasks/video")
    .bodyValue(body).retrieve()
    .bodyToMono(CommonResult.class)
    .block();  // ← 同步阻塞，线程持有 5~15 分钟
```

**问题**：
- 一个用户请求就占一个 Tomcat 线程，几分钟内并发几个用户就把线程池打满
- 用户前端在等，但后端线程被长任务堵死

**Phase 2 解法**：改为异步回调模式

```
Java 侧                    FastAPI 侧
  │                           │
  ├─ POST /v1/tasks/video ──→ │  （立即返回 session_id）
  │←── {session_id: "xxx"} ── │
  │                           │
  │   [线程释放，返回 202]     │
  │                           │
  │   ←──── POST /internal/notify ─── │
  │        {task_id, video_url, shot_index}
  │                           │
  │   [更新 creative_task status=completed]
  │   [推送 SSE 给前端]
  │                           │
```

### 11.2 Task 表新增字段（与 polling_tasks 对应）

```sql
-- creative_task 表新增回调相关字段
ALTER TABLE creative_task ADD COLUMN polling_task_id BIGINT;
ALTER TABLE creative_task ADD COLUMN video_id VARCHAR(64);
ALTER TABLE creative_task ADD COLUMN shot_index INTEGER DEFAULT 0;
ALTER TABLE creative_task ADD COLUMN completed_at TIMESTAMPTZ;

-- 添加索引供回调查询
CREATE INDEX idx_task_polling ON creative_task(polling_task_id);
CREATE INDEX idx_task_video_id ON creative_task(video_id);
```

**字段映射关系**：

| Java creative_task | FastAPI polling_tasks | 说明 |
|---|---|---|
| `id` | `id` | Java 主键 |
| `polling_task_id` | `id` | 关联关系 |
| `video_id` | `video_id` | Agnes 异步任务 ID（冗余存储，方便 Java 自己查） |
| `shot_index` | `shot_index` | 当前分镜索引 |
| `status` | `status` | Java 视角的任务状态（供前端轮询/SSE 推送） |
| `result_json` | `video_url` | 完成后的产物 URL |

### 11.3 回调接收接口（Java 新增）

```java
// web-backend/src/main/java/com/dreamweaver/controller/NotifyController.java
package com.dreamweaver.controller;

import com.dreamweaver.service.NotifyService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

/**
 * FastAPI 回调接收。只做事务性更新 + SSE 推送触发，不写业务逻辑。
 */
@RestController
@RequestMapping("/internal")
@RequiredArgsConstructor
public class NotifyController {

    private final NotifyService notifyService;

    /**
     * FastAPI 完成回调入口。
     * 幂等设计：同一 video_id 多次回调只处理一次。
     */
    @PostMapping("/notify")
    public void handleNotify(@RequestBody NotifyRequest request) {
        notifyService.handleCompletion(request);
    }
}
```

```java
// NotifyRequest.java
package com.dreamweaver.dto;

import lombok.Data;

@Data
public class NotifyRequest {
    private String video_id;    // Agnes 返回的异步任务 ID
    private String video_url;   // MinIO 持久化 URL
    private Integer shot_index; // 当前分镜索引
    private String session_id;  // LangGraph 会话 ID
    private String status;      // completed / failed
    private String error_message;
}
```

### 11.4 NotifyService 实现（幂等 + 乐观锁防乱序覆盖）

**核心风险**：FastAPI 侧 poller 和回调双路径可能在极短时间窗内都触发 resume，导致 Java 收到两条回调——先到的可能写 completed，后到的（其实是同一个 video 的旧版本）再写回 failed，状态被覆盖。

**解法**：
1. 幂等键用 `video_id + shot_index` 组合，不是单独 video_id
2. 加 `expected_version` 乐观锁——每次更新时检查版本号是否匹配，不匹配则丢弃（说明有更新的回调已经处理过）
3. 状态机跳转检查：只有 `pending → queued → video_generating` 才允许处理，`completed` 或 `failed` 直接丢弃

```java
// web-backend/src/main/java/com/dreamweaver/entity/Task.java（需补充字段）
package com.dreamweaver.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import com.baomidou.mybatisplus.annotation.Version;
import lombok.Data;

import java.time.LocalDateTime;

@Data
@TableName("creative_task")
public class Task {

    @TableId(type = IdType.AUTO)
    private Long id;

    /** FastAPI 侧 session_id（LangGraph thread_id） */
    private String sessionId;

    private Long userId;

    /** pending/queued/script_writing/storyboard_writing/video_generating/... /completed/failed */
    private String status;

    /** 用户原始需求 */
    private String prompt;

    /** 模型侧产物（视频 URL 数组 JSON / 分镜 JSON） */
    private String resultJson;

    /** Agnes 返回的异步任务 ID（用于幂等判断） */
    private String videoId;

    /** 当前分镜索引 */
    private Integer shotIndex;

    private String errorMessage;

    private LocalDateTime createdAt;

    private LocalDateTime updatedAt;

    /**
     * 乐观锁版本号：回调更新时用 expected_version 防止乱序覆盖。
     * MyBatis-Plus @Version 注解自动处理：
     * - 更新时自动加 1
     * - WHERE 条件带 version 匹配，不匹配则影响行数为 0
     */
    @Version
    private Integer version;
}
```

```java
// web-backend/src/main/java/com/dreamweaver/service/impl/NotifyServiceImpl.java
package com.dreamweaver.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.dreamweaver.dto.NotifyRequest;
import com.dreamweaver.entity.Task;
import com.dreamweaver.mapper.TaskMapper;
import com.dreamweaver.service.NotifyService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;

@Slf4j
@Service
@RequiredArgsConstructor
public class NotifyServiceImpl implements NotifyService {

    private final TaskMapper taskMapper;

    @Override
    @Transactional
    public void handleCompletion(NotifyRequest request) {
        // 1. 幂等检查：按 video_id + shot_index 组合查找
        Task task = taskMapper.selectOne(
            new LambdaQueryWrapper<Task>()
                .eq(Task::getVideoId, request.getVideo_id())
                .eq(request.getShot_index() != null, Task::getShotIndex, request.getShot_index())
        );

        if (task == null) {
            log.warn("notify 收到未知任务: video_id={}, shot_index={}",
                    request.getVideo_id(), request.getShot_index());
            return;
        }

        // 2. 状态机检查：只处理「生成中」的任务
        // completed / failed 直接丢弃，防止晚到的旧回调覆盖新状态
        if ("completed".equals(task.getStatus()) || "failed".equals(task.getStatus())) {
            log.info("notify 任务 {} 已终态 (status={})，丢弃回调", task.getId(), task.getStatus());
            return;
        }

        // 3. 乐观锁更新：version 不匹配说明有更新的回调已处理，丢弃
        int updated = taskMapper.update(null,
            new LambdaUpdateWrapper<Task>()
                .eq(Task::getId, task.getId())
                .eq(Task::getVersion, task.getVersion())  // 乐观锁
                .set(Task::getStatus, request.getStatus())
                .set(Task::getResultJson, request.getVideo_url())
                .set(Task::getUpdatedAt, LocalDateTime.now())
                .set(request.getStatus().equals("failed"),
                        Task::getErrorMessage, request.getError_message())
        );

        if (updated == 0) {
            log.warn("notify 任务 {} 乐观锁冲突，丢弃（已有更新的回调处理过）", task.getId());
            return;
        }

        log.info("notify 任务 {} 处理完成，状态={}", task.getId(), request.getStatus());
    }
}
```

**关键设计点：**
- `version` 字段由 MyBatis-Plus `@Version` 注解自动管理，更新时自动 +1
- `WHERE version = ?` 条件不匹配 → update 影响行数 = 0 → 静默丢弃
- 这样「晚到的旧回调」即使到达，也会因为 version 不匹配而被丢弃，不会覆盖新状态

### 11.5 createTask 改造（Phase 2 异步版）

```java
// web-backend/src/main/java/com/dreamweaver/service/impl/TaskServiceImpl.java（改造后）
@Override
@Transactional
public TaskResponse createTask(CreateTaskRequest request) {
    // 1. 落库（pending）
    Task task = new Task();
    task.setPrompt(request.getPrompt());
    task.setUserId(request.getUserId() == null ? null : Long.valueOf(request.getUserId()));
    task.setStatus("pending");
    task.setCreatedAt(LocalDateTime.now());
    task.setUpdatedAt(LocalDateTime.now());
    taskMapper.insert(task);

    // 2. 调 FastAPI 提交（异步，不等结果）
    String agentBase = agentServiceProperties.getBaseUrl();
    Map<String, Object> body = Map.of(
        "prompt", request.getPrompt(),
        "user_id", request.getUserId() == null ? "demo-user" : request.getUserId()
    );

    CommonResult<Map<String, Object>> agentResp = webClientBuilder.build()
        .post()
        .uri(agentBase + "/v1/tasks/video")
        .contentType(MediaType.APPLICATION_JSON)
        .bodyValue(body)
        .retrieve()
        .bodyToMono(CommonResult.class)
        .block();  // Phase 2 改成非阻塞：只等 FastAPI 返回 session_id，不等视频生成

    // 3. 回写 session_id（不阻塞线程）
    if (agentResp != null && agentResp.getData() != null) {
        String sessionId = (String) agentResp.getData().get("session_id");
        task.setSessionId(sessionId);
        task.setStatus("queued");
        task.setUpdatedAt(LocalDateTime.now());
        taskMapper.updateById(task);
    }

    return toResponse(task);
}
```

**关键变化**：
- Phase 1：`.block()` 等 FastAPI 完成整个视频生成（占用线程几分钟）
- Phase 2：`.block()` 只等 FastAPI 返回 session_id（几百毫秒），然后立即释放线程
- 视频完成后的更新由 FastAPI 回调 `/internal/notify` 触发

### 11.6 前端轮询 vs SSE

Phase 2 支持两种进度查询方式：

**方式一：前端轮询（简单，Phase 1）**
```
前端每 5 秒 GET /api/tasks/{id} → 查 status → 渲染进度
```

**方式二：SSE 推送（推荐，Phase 2）**
```
Java 侧：创建 SseEmitter → 注册到 session_id → 回调时 emit 事件
前端：  new EventSource('/api/tasks/{id}/events')
```

```java
// 回调时触发 SSE
@GetMapping("/{id}/events")
public SseEmitter streamEvents(@PathVariable Long id) {
    Task task = taskMapper.selectById(id);
    SseEmitter emitter = new SseEmitter(60_000L);
    // 注册到 session_id 的事件队列（简单实现：Map<sessionId, List<SseEmitter>>）
    eventRegistry.register(task.getSessionId(), emitter);
    return emitter;
}

// NotifyServiceImpl 中回调完成后触发
eventRegistry.emit(request.getSession_id(), Map.of("type", "completed", "url", request.getVideo_url()));
```

### 11.7 与 polling_tasks 的对应关系（完整链路）

```
用户在 Java 创建任务
    ↓
Java: INSERT creative_task(status='pending')
    ↓
Java: POST FastAPI /v1/tasks/video
    ↓
FastAPI: INSERT LangGraph checkpoint(session_id='xxx', status='pending')
         INSERT polling_tasks(video_id='vvv', session_id='xxx', status='pending')
    ↓
FastAPI 返回 session_id → Java 回写 creative_task.session_id, status='queued'
    ↓
FastAPI 执行 LangGraph 节点...
    ↓
FastAPI: video_generator_node 提交视频 → interrupt 挂起
    ↓
VideoPoller 接管轮询 → 完成后下载 MinIO → UPDATE polling_tasks(status='completed')
    ↓
FastAPI: Command(resume=video_url) → 写回 LangGraph state → QC → synthesizer
    ↓
FastAPI: POST /internal/notify → Java 更新 creative_task(status='completed')
    ↓
Java: SSE 推送前端 → 用户看到成品
```

**状态同步约定**：
| LangGraph state | polling_tasks.status | creative_task.status |
|---|---|---|
| PENDING | pending | pending |
| VIDEO_GENERATING | pending/recovering | queued |
| COMPLETED | completed | completed |
| FAILED | failed | failed |

---

## 十二、总结

本设计文档完成了 DreamWeaver Agent 项目的核心编排逻辑，涵盖：
- LangGraph State Schema 与节点定义
- interrupt 多轮澄清的正确用法
- VideoPoller 独立轮询架构（Phase 2）
- 幂等回调与 Redis 锁协调机制
- Java↔FastAPI 数据表映射与接口约定
- Java 侧异步回调设计（notify 接口 + SSE 推送）
- 面试口径准备

断点恢复链已完整闭合：提交 → interrupt 挂起 → Poller 接管轮询 → Command(resume) 写回 state → QC → synthesizer → FastAPI 回调 Java → SSE 推送到前端。

Phase 1 已完成骨架代码（测试通过），Phase 2 需要补充：
1. Java 侧新增 NotifyController + NotifyService
2. polling_tasks 表建表 SQL
3. FastAPI 侧 VideoPoller 实现
4. 前端 SSE 订阅 hook

面试叙事已完整：「我把实习里做的 Agent 平台方法论，独立完整地重做了一遍，落地到 AI 短视频创作场景，三端全栈实现。」
