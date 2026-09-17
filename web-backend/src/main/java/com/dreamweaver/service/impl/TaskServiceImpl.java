package com.dreamweaver.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.dreamweaver.common.CommonResult;
import com.dreamweaver.config.AgentServiceProperties;
import com.dreamweaver.dto.CreateTaskRequest;
import com.dreamweaver.dto.TaskListResponse;
import com.dreamweaver.dto.TaskResponse;
import com.dreamweaver.common.UserContext;
import com.dreamweaver.entity.Task;
import com.dreamweaver.mapper.TaskMapper;
import com.dreamweaver.service.TaskService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.reactive.function.client.WebClient;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 任务服务实现。
 * 编排：落库 creative_task(pending) → 调 FastAPI /v1/tasks/video → 回写 session_id。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class TaskServiceImpl implements TaskService {

    /** 当前用户（任务归属 + 配额统计用） */
    private final UserContext userContext;

    private final TaskMapper taskMapper;
    private final AgentServiceProperties agentServiceProperties;
    private final WebClient.Builder webClientBuilder;
    private final StuckTaskWatchdog stuckTaskWatchdog;
    private final TaskJsonCodec taskJsonCodec;
    private final SegmentReworkPlanner reworkPlanner;

    /** 终态集合：可直接删除 / 可重新生成 */
    private static final Set<String> TERMINAL_STATUSES = Set.of("completed", "failed", "expired");

    @Override
    @Transactional
    public TaskResponse createTask(CreateTaskRequest request) {
        return submitNewTask(request);
    }

    @Override
    public TaskResponse getTask(Long id) {
        Task task = taskMapper.selectById(id);
        return task == null ? null : toResponse(task);
    }

    /**
     * 来源过滤形态。
     *
     * <p>抽成枚举 + 纯函数，只为一个原因：判断条件错了**不会报错**，只会静默少返回/多返回。
     * 而它现在同时服务三条链路 —— 画廊（不传 source）、画布素材面板（asset）、画布作品面板（work）。
     */
    enum SourceFilter {
        /** 不按来源筛（素材 + 作品都要） */
        NO_FILTER,
        /** 排除画布素材（画廊默认；source=work 同义） */
        EXCLUDE_ASSETS,
        /** 只要画布素材 */
        ASSETS_ONLY,
    }

    static SourceFilter sourceFilterOf(String source, boolean includeAssets) {
        if ("asset".equalsIgnoreCase(source)) {
            return SourceFilter.ASSETS_ONLY;
        }
        if ("work".equalsIgnoreCase(source)) {
            return SourceFilter.EXCLUDE_ASSETS;
        }
        // 不传 / 未知取值 → 维持老语义（画廊默认排除素材，面板传 includeAssets=true 时都返回）
        return includeAssets ? SourceFilter.NO_FILTER : SourceFilter.EXCLUDE_ASSETS;
    }

    /**
     * LIKE 模式转义：用户输入里的 {@code %} / {@code _} / {@code \} 不能当通配符用。
     *
     * <p>不转义的后果很隐蔽：搜一个「%」会把全部记录都匹配上（看着像搜索没生效），
     * 搜「_」同理。MySQL 的 LIKE 默认转义符就是反斜杠，所以在这里加反斜杠即可。
     */
    static String escapeLike(String keyword) {
        return keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_");
    }

    @Override
    public TaskListResponse listTasks(int page, int size, String genType, Boolean draft,
            boolean includeAssets, String status, String source, String keyword) {
        int safePage = Math.max(page, 1);
        int safeSize = Math.min(Math.max(size, 1), 50);
        // count 与 list 各建一次（wrapper 可变，共用会把 LIMIT 带进 count）
        long total = taskMapper.selectCount(
                listCondition(genType, draft, includeAssets, status, source, keyword));
        List<TaskResponse> list = taskMapper.selectList(
                listCondition(genType, draft, includeAssets, status, source, keyword)
                        .orderByDesc(Task::getId)
                        .last("LIMIT " + safeSize + " OFFSET " + ((long) (safePage - 1) * safeSize))
        ).stream().map(this::toResponse).toList();
        TaskListResponse resp = new TaskListResponse();
        resp.setList(list);
        resp.setTotal(total);
        resp.setPage(safePage);
        resp.setSize(safeSize);
        return resp;
    }

    /** 列表查询条件（count 与 list 共用一处，避免两侧条件漂移） */
    private LambdaQueryWrapper<Task> listCondition(String genType, Boolean draft, boolean includeAssets,
            String status, String source, String keyword) {
        SourceFilter filter = sourceFilterOf(source, includeAssets);
        boolean hasKeyword = keyword != null && !keyword.isBlank();
        return new LambdaQueryWrapper<Task>()
                .eq(genType != null && !genType.isBlank(), Task::getGenType, genType)
                // draft 为 null = 不按草稿筛选；true/false = 只取草稿/只取成品
                .eq(draft != null, Task::getIsDraft, draft != null && draft ? 1 : 0)
                .eq(status != null && !status.isBlank(), Task::getStatus, status)
                .eq(filter == SourceFilter.ASSETS_ONLY, Task::getSource, "canvas_asset")
                .and(filter == SourceFilter.EXCLUDE_ASSETS, w -> w.isNull(Task::getSource)
                        .or().ne(Task::getSource, "canvas_asset"))
                // 关键字：按需求原文/提示词模糊匹配（转义见 escapeLike）
                .like(hasKeyword, Task::getPrompt, hasKeyword ? escapeLike(keyword.trim()) : null);
    }

    @Override
    @Transactional
    public void deleteTask(Long id) {
        Task task = taskMapper.selectById(id);
        if (task == null) {
            throw new IllegalArgumentException("任务不存在（id=" + id + "）");
        }
        // 非终态任务带 session_id → 先通知 Agent 取消排队/停止继续生成，再删除本地记录
        if (!TERMINAL_STATUSES.contains(task.getStatus())
                && task.getSessionId() != null && !task.getSessionId().isBlank()) {
            cancelAgentSession(task.getSessionId());
        }
        taskMapper.deleteById(id);
        // 任务已从 DB 删除，解除 Redis 看门狗避免误转 failed
        stuckTaskWatchdog.clear(id);
        log.info("删除任务: id={}, status={}, prompt={}", id, task.getStatus(), task.getPrompt());
    }

    /** 通知 FastAPI 取消排队中的会话。运行中会话由 Agent 侧 409 拒绝（不非法打断生成），仅记日志。 */
    private void cancelAgentSession(String sessionId) {
        try {
            webClientBuilder.build()
                    .post()
                    .uri(agentServiceProperties.getBaseUrl() + "/v1/tasks/" + sessionId + "/cancel")
                    .retrieve()
                    .bodyToMono(String.class)
                    .block();
        } catch (Exception e) {
            // 409 已开始执行 / 404 会话不存在 / 网络异常：都不阻塞本地删除
            log.info("Agent 取消会话 {} 响应: {}", sessionId, e.getMessage());
        }
    }

    /**
     * 「重新生成」前先取消 Agent 侧的旧会话（P1-3）。
     *
     * <p>不取消的话，旧会话（尤其 Agent 重启后自动恢复的那些）会继续跑到结束：
     * 回调按 session_id 查任务时，因 session_id 已被新会话覆盖而查不到，结果被丢弃
     * —— 纯属白烧 agnes 额度。
     *
     * <p>注意 Agent 侧只能取消「排队中」的会话；已在运行的由 Agent 的心跳探测
     * （/internal/heartbeat 回 tracked=false）自行中止，两者互补。
     * 终态任务的旧会话必然已结束，直接跳过。
     */
    private void cancelOldAgentSession(Task task) {
        if (task == null || task.getSessionId() == null || task.getSessionId().isBlank()) {
            return;
        }
        if (TERMINAL_STATUSES.contains(task.getStatus())) {
            return;
        }
        cancelAgentSession(task.getSessionId());
        log.info("重新提交前取消旧 Agent 会话: taskId={}, sessionId={}",
                task.getId(), task.getSessionId());
    }

    @Override
    @Transactional
    public TaskResponse regenerateTask(Long id) {
        // 单参重载保留给 TaskAutoRetryer 等旧调用方：不覆盖任何精细控制参数
        return regenerateTask(id, null);
    }

    /**
     * 把任务重置为「待派发」的公共部分（全量重生 / 段重生共用）。
     *
     * <p>原先这两条链路各写一遍约 12 行几乎相同的 {@code LambdaUpdateWrapper}，差异只在
     * 段配置与参数两三个字段上。合并成一处后，新增「重置时要清 / 要保留什么」只需要改这里，
     * 不会再出现「改了一条链路忘了另一条」。
     *
     * <p>两条链路的差异由调用方追加（返回的 wrapper 可以继续 {@code .set(...)}）：
     * <ul>
     *   <li>全量重生：清 {@code image_urls} 与 {@code segments_json}，写 {@code gen_params_json}</li>
     *   <li>段重生：写新的 {@code segments_json}（保留其他字段）</li>
     * </ul>
     *
     * <p>⚠️ 为什么必须用 wrapper 而不是 {@code updateById}：后者的
     * {@code FieldStrategy.NOT_NULL} 会**忽略 null 字段**，显式置空根本不生效。
     *
     * @param prevResultJson 旧产物（存 {@code prev_result_json} 供回滚）；可为 null
     */
    private com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper<Task>
            resetTaskForRerun(Long id, String prevResultJson) {
        return new com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper<Task>()
                .eq(Task::getId, id)
                .set(Task::getStatus, "pending")
                .set(Task::getSessionId, null)
                .set(Task::getResultJson, null)
                .set(Task::getErrorMessage, null)
                .set(Task::getCompletedAt, null)
                // 计时打点清零：本轮重新派发时由 dispatchToAgent 重新写 started_at，
                // 否则派发失败会把上一次的生成耗时留在画廊上（耗时口径见 phase8_migration.sql）
                .set(Task::getStartedAt, null)
                .set(Task::getPrevResultJson, prevResultJson)
                .set(Task::getUpdatedAt, LocalDateTime.now());
    }

    @Override
    @Transactional
    public TaskResponse regenerateTask(Long id, CreateTaskRequest override) {
        Task original = taskMapper.selectById(id);
        if (original == null) {
            throw new IllegalArgumentException("任务不存在（id=" + id + "）");
        }
        // interrupted = 看门狗兜底出的非终态（Agent 可能已失联/恢复失败）：
        // 不在终态集合里，但仍必须允许用户「重新生成」原地重跑，否则该状态无恢复出口
        if (!TERMINAL_STATUSES.contains(original.getStatus())
                && !"interrupted".equals(original.getStatus())) {
            throw new IllegalArgumentException(
                    "任务正在生成中（status=" + original.getStatus() + "），无法重新生成");
        }

        // 先取消 Agent 侧的旧会话（P1-3）：interrupted 任务可能已被 Agent 自动恢复、
        // 正在后台继续生成，不取消就会白跑一遍且回调因 session_id 被覆盖而丢弃
        cancelOldAgentSession(original);

        CreateTaskRequest request = new CreateTaskRequest();
        request.setPrompt(original.getPrompt());
        request.setGenType(original.getGenType());
        request.setUserId(original.getUserId() == null ? null : String.valueOf(original.getUserId()));
        // 还原精细控制参数（风格/负面词/时间轴/元素绑定），否则重生成会丢设定
        taskJsonCodec.applyGenParamsJson(original.getGenParamsJson(), request);
        // 用户在画廊「编辑参数」里改过的值覆盖历史值（非空字段才覆盖）
        int overridden = applyOverride(override, request);
        // 覆盖后重新序列化：落库让下一次重生成（无论走哪个入口）都带上新值
        String genParamsJson = taskJsonCodec.buildGenParamsJson(request);

        // 同一任务原地重新生成：清空旧产物与错误，保留 id/prompt/genType/userId，
        // 重新走 提交→排队→生成→回调 链路（不再创建新任务 id）
        taskMapper.update(null, resetTaskForRerun(id, original.getResultJson())
                // 全量重生成：段配置失效一并清空，旧产物已存入 prev_result_json 供回滚
                .set(Task::getImageUrls, null)
                .set(Task::getSegmentsJson, null)
                // 本次编辑后的参数落库（全空时 buildGenParamsJson 返回 null，同样清掉旧值）
                .set(Task::getGenParamsJson, genParamsJson));
        // 内存态同步：dispatchToAgent 里的 updateById(original) 会把 entity 上的
        // gen_params_json 一并写回库，不刷新这里就会用旧值覆盖刚写入的新参数
        original.setGenParamsJson(genParamsJson);

        log.info("重新生成任务: id={} 原地重跑 prompt={} 覆盖字段数={} genParamsJson={}",
                id, original.getPrompt(), overridden, genParamsJson);
        return dispatchToAgent(original, request);
    }

    /**
     * 把画廊「编辑参数」提交的 override 覆盖到重建出的请求上。
     * 只覆盖非空字段（null / 空白 / 非正数视为「未填」，保留历史设定）；
     * 不改 prompt/genType/userId——那些由 entity 决定。
     *
     * @return 实际覆盖的字段个数（仅用于日志）
     */
    private int applyOverride(CreateTaskRequest override, CreateTaskRequest request) {
        if (override == null) {
            return 0;
        }
        int count = 0;
        if (override.getStylePrompt() != null && !override.getStylePrompt().isBlank()) {
            request.setStylePrompt(override.getStylePrompt());
            count++;
        }
        if (override.getNegativePrompt() != null && !override.getNegativePrompt().isBlank()) {
            request.setNegativePrompt(override.getNegativePrompt());
            count++;
        }
        if (override.getTotalSeconds() != null && override.getTotalSeconds() > 0) {
            request.setTotalSeconds(override.getTotalSeconds());
            count++;
        }
        if (override.getShotCount() != null && override.getShotCount() > 0) {
            request.setShotCount(override.getShotCount());
            count++;
        }
        if (override.getShotLanguage() != null && !override.getShotLanguage().isBlank()) {
            request.setShotLanguage(override.getShotLanguage());
            count++;
        }
        if (override.getReferenceBindings() != null && !override.getReferenceBindings().isBlank()) {
            request.setReferenceBindings(override.getReferenceBindings());
            count++;
        }
        return count;
    }

    /**
     * 统一提交链路（新建）：落库 pending → 调 FastAPI → 回写 session_id。
     */
    /**
     * 画布片段参考图必须是公网 URL（agnès 拒收 localhost/内网/base64 之外形态），
     * 本地上传图只能预览；发现有内网/本地 URL 直接拒绝，避免生成环节 400。
     */
    private void validateSegmentUrlsPublic(String segmentsJson) {
        if (segmentsJson == null || segmentsJson.isBlank()) {
            return;
        }
        try {
            java.util.List<java.util.Map<String, Object>> segs =
                    taskJsonCodec.parseSegments(segmentsJson);
            for (java.util.Map<String, Object> seg : segs) {
                Object u = seg.get("image_url");
                if (u == null) {
                    continue;
                }
                String url = String.valueOf(u).trim().toLowerCase();
                if (url.startsWith("http://localhost")
                        || url.startsWith("http://127.")
                        || url.startsWith("http://10.")
                        || url.startsWith("http://192.168.")
                        || url.startsWith("http://172.")) {
                    throw new IllegalArgumentException(
                            "画布片段包含本地上传/内网图片，agnès 无法生成：请改用历史作品或「文生图」产出（提示词→生成）");
                }
            }
        } catch (IllegalArgumentException e) {
            throw e;
        } catch (Exception e) {
            log.warn("segments 校验解析失败: {}", e.getMessage());
        }
    }

    private TaskResponse submitNewTask(CreateTaskRequest request) {
        // 1. 落库（pending）
        Task task = new Task();
        task.setPrompt(request.getPrompt());
        // userId 缺失时回落当前用户：此前一律落 null，而配额累加是「userId 非空才计」，
        // 于是 api_quota 表永远是空的（谁也统计不到）。
        task.setUserId(request.getUserId() == null || request.getUserId().isBlank()
                ? userContext.currentUserId() : Long.valueOf(request.getUserId()));
        task.setStatus("pending");
        task.setGenType(request.getGenType() != null ? request.getGenType() : "text_video");
        // 来源标记：默认 default（进画廊）；画布素材传 canvas_asset（画廊过滤）
        task.setSource(request.getSource() == null || request.getSource().isBlank()
                ? "default" : request.getSource().trim());
        // 段配置落库：重生时取此作为输入源（未勾选段复用已有视频、勾选段重新生成）
        task.setSegmentsJson(request.getSegments());
        // 精细控制参数落库：regenerate 从 entity 重建请求时需要还原
        task.setGenParamsJson(taskJsonCodec.buildGenParamsJson(request));
        task.setCreatedAt(LocalDateTime.now());
        task.setUpdatedAt(LocalDateTime.now());
        taskMapper.insert(task);
        return dispatchToAgent(task, request);
    }

    /**
     * 调 FastAPI 提交任务并回写状态：成功后 queued + 武装看门狗；
     * agent 不可达转 failed；无 session_id（如队列满）保持 pending 由看门狗兜底。
     * createTask 与 regenerateTask 共用。
     */
    private TaskResponse dispatchToAgent(Task task, CreateTaskRequest request) {
        validateSegmentUrlsPublic(request.getSegments());
        String agentBase = agentServiceProperties.getBaseUrl();
        Map<String, Object> body = new java.util.HashMap<>();
        body.put("prompt", request.getPrompt());
        body.put("user_id", request.getUserId() == null ? "demo-user" : request.getUserId());
        if (request.getGenType() != null) {
            body.put("gen_type", request.getGenType());
        }
        if (request.getReferenceImages() != null && !request.getReferenceImages().isBlank()) {
            body.put("reference_images", request.getReferenceImages());
        }
        if (request.getSegments() != null && !request.getSegments().isBlank()) {
            body.put("segments", request.getSegments());
        }
        if (request.getVideoModel() != null && !request.getVideoModel().isBlank()) {
            body.put("video_model", request.getVideoModel());
        }
        if (request.getSlideshowImages() != null && !request.getSlideshowImages().isBlank()) {
            body.put("slideshow_images", request.getSlideshowImages());
        }
        if (request.getSlideSeconds() != null) {
            body.put("slide_seconds", request.getSlideSeconds());
        }
        // 可灵式精细控制：风格/负面词/时间轴/元素绑定
        if (request.getStylePrompt() != null && !request.getStylePrompt().isBlank()) {
            body.put("style_prompt", request.getStylePrompt());
        }
        if (request.getNegativePrompt() != null && !request.getNegativePrompt().isBlank()) {
            body.put("negative_prompt", request.getNegativePrompt());
        }
        if (request.getTotalSeconds() != null) {
            body.put("total_seconds", request.getTotalSeconds());
        }
        if (request.getShotCount() != null) {
            body.put("shot_count", request.getShotCount());
        }
        if (request.getShotLanguage() != null && !request.getShotLanguage().isBlank()) {
            body.put("shot_language", request.getShotLanguage());
        }
        // 直出图：画布节点「一键文生图」专用，跳过 agent 侧流水线直接出图
        if (request.getDirectImage() != null && request.getDirectImage()) {
            body.put("direct_image", Boolean.TRUE);
            body.put("image_count", request.getImageCount() == null ? 1 : request.getImageCount());
        }
        if (request.getReferenceBindings() != null && !request.getReferenceBindings().isBlank()) {
            body.put("reference_bindings", request.getReferenceBindings());
        }

        CommonResult<Map<String, Object>> agentResp = null;
        try {
            agentResp = webClientBuilder.build()
                    .post()
                    .uri(agentBase + "/v1/tasks/video")
                    .contentType(MediaType.APPLICATION_JSON)
                    .bodyValue(body)
                    .retrieve()
                    .bodyToMono(CommonResult.class)
                    .block();
        } catch (Exception e) {
            // FastAPI 不可达：如实落 failed，前端可删除/重新提交；否则任务会静默卡 pending。
            // 原生异常文本只进日志，用户侧统一显示友好文案（全局异常类兜底原则）。
            log.warn("调 FastAPI 提交失败，任务 {} 转 failed: {}", task.getId(), e.getMessage(), e);
            task.setStatus("failed");
            task.setErrorMessage(friendlyAgentErrorMessage(e));
            task.setUpdatedAt(LocalDateTime.now());
            taskMapper.updateById(task);
            return toResponse(task);
        }

        // 3. 回写 session_id
        if (agentResp != null && agentResp.getData() != null) {
            String sessionId = (String) agentResp.getData().get("session_id");
            task.setSessionId(sessionId);
            task.setStatus("queued");
            // 生成计时起点：Agent 已受理本轮生成（拿到 session_id）。
            // 画廊「耗时」= completed_at - started_at，排队等待/停机/中断空档都不计入。
            // 新建、全量重生、段重生三条链路都汇聚到此处，一处理即可全覆盖。
            task.setStartedAt(LocalDateTime.now());
            // 内存态清空旧产物/错误：updateById 的 NOT_NULL 策略会忽略 null，
            // 但会把 entity 里残留的旧值（regenerate 时加载的）重新写回——必须先置 null 挡掉
            task.setErrorMessage(null);
            task.setResultJson(null);
            task.setImageUrls(null);
            task.setUpdatedAt(LocalDateTime.now());
            taskMapper.updateById(task);
            // 武装 Redis TTL 看门狗：视频任务 30 分钟、其余 10 分钟无回调自动转 failed
            stuckTaskWatchdog.watch(task.getId(), task.getGenType());
        } else {
            // agent 响应了但没有 session_id（如队列满 429）：武装看门狗等待自愈
            stuckTaskWatchdog.watch(task.getId(), task.getGenType());
            log.warn("调 FastAPI 提交未返回 session_id，任务 {} 保持 pending 由看门狗兜底", task.getId());
        }

        return toResponse(task);
    }

    /**
     * 把 agent 提交阶段异常翻译成用户可读文案（原生细节只进日志，不暴露给前端）。
     * 与 agent-service 全局异常层的友好化保持一致的原因分类。
     */
    private String friendlyAgentErrorMessage(Exception e) {
        String msg = e.getMessage() == null ? "" : e.getMessage().toLowerCase();
        if (msg.contains("connection refused") || msg.contains("connectexception")) {
            return "Agent 服务暂不可用，请稍后重试";
        }
        if (msg.contains("timed out") || msg.contains("timeout")) {
            return "Agent 服务响应超时，请稍后重试";
        }
        if (msg.contains("unauthorized") || msg.contains(" 403")) {
            return "Agent 服务鉴权失败，请联系管理员";
        }
        if (msg.contains(" 429")) {
            return "Agent 任务队列繁忙，请稍后重试";
        }
        return "Agent 服务处理失败，请稍后重试";
    }

    /**
     * 拼接成片：把该任务的 N 段视频拼成一条长视频（标准模式此前没有任何用户入口）。
     *
     * <p>此前拼接能力只服务于两条自动链路——画布模式（segments → synthesizer）、
     * 图片合成视频（image_slideshow）；「一句话生成」出来的分段只能平铺看，
     * 用户点不到「拼成一条」。这里补上入口：调 Agent 的
     * {@code POST /v1/tasks/{sessionId}/concat}，把返回的本地产物 URL 插到
     * {@code result_json} 首位（前端 {@code finalVideoUrl()} 按 {@code /v1/files/}
     * 前缀识别成片，插首位即自动切成「成片 + 分段缩略」布局）。
     *
     * <p>不消耗 agnes 额度（纯本地 ffmpeg），且幂等：已有成片直接返回。
     */
    @Override
    @Transactional
    public TaskResponse concatTask(Long id) {
        Task task = taskMapper.selectById(id);
        if (task == null) {
            throw new IllegalArgumentException("任务不存在（id=" + id + "）");
        }
        if (!TERMINAL_STATUSES.contains(task.getStatus())) {
            throw new IllegalArgumentException(
                    "仅已终态的任务可拼接成片（当前=" + task.getStatus() + "）");
        }
        if (taskJsonCodec.hasFinalVideo(task.getResultJson())) {
            return toResponse(task);
        }
        List<String> segments = taskJsonCodec.parseResultUrls(task.getResultJson());
        if (segments.size() < 2) {
            throw new IllegalArgumentException("分段不足 2 个，无需拼接成片");
        }
        if (task.getSessionId() == null || task.getSessionId().isBlank()) {
            throw new IllegalArgumentException("任务未关联 Agent 会话，无法拼接成片");
        }
        String finalUrl = callAgentConcat(task.getSessionId());
        List<String> merged = new java.util.ArrayList<>();
        merged.add(finalUrl);
        merged.addAll(segments);
        taskMapper.update(null,
                new com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper<Task>()
                        .eq(Task::getId, id)
                        .set(Task::getResultJson, taskJsonCodec.toJsonString(merged))
                        .set(Task::getUpdatedAt, LocalDateTime.now()));
        log.info("拼接成片: id={} 段数={} finalUrl={}", id, segments.size(), finalUrl);
        return toResponse(taskMapper.selectById(id));
    }

    /** 调 Agent 拼接端点，返回本地产物 URL（agent 侧落 final.mp4 到会话目录） */
    private String callAgentConcat(String sessionId) {
        try {
            CommonResult<?> resp = webClientBuilder.build()
                    .post()
                    .uri(agentServiceProperties.getBaseUrl() + "/v1/tasks/" + sessionId + "/concat")
                    .retrieve()
                    .bodyToMono(CommonResult.class)
                    .block(java.time.Duration.ofMinutes(5));
            Object data = resp == null ? null : resp.getData();
            Object url = (data instanceof Map<?, ?> m) ? m.get("final_url") : null;
            if (url == null || String.valueOf(url).isBlank()) {
                throw new IllegalStateException("Agent 未返回成片 URL");
            }
            return String.valueOf(url);
        } catch (Exception e) {
            log.warn("调 Agent 拼接失败 sessionId={}: {}", sessionId, e.getMessage());
            throw new IllegalArgumentException(friendlyAgentErrorMessage(e));
        }
    }

    @Override
    @Transactional
    public TaskResponse reworkTask(Long id, List<Integer> reworkIndices,
            Map<String, String> editedPrompts) {
        return doReworkCore(id, reworkIndices, editedPrompts);
    }

    @Override
    public com.dreamweaver.dto.BatchReworkResult batchRework(
            com.dreamweaver.dto.BatchReworkRequest request) {
        List<com.dreamweaver.dto.BatchReworkItem> items = request.getItems();
        List<com.dreamweaver.dto.BatchReworkItemResult> results = new java.util.ArrayList<>();
        int successCount = 0, failedCount = 0;
        for (com.dreamweaver.dto.BatchReworkItem item : items) {
            com.dreamweaver.dto.BatchReworkItemResult r =
                    new com.dreamweaver.dto.BatchReworkItemResult();
            r.setTaskId(item.getTaskId());
            try {
                TaskResponse resp = doReworkCore(item.getTaskId(),
                        item.getReworkIndices(), item.getEditedPrompts());
                r.setSuccess(true);
                r.setMessage("OK");
                r.setTask(resp);
                successCount++;
            } catch (Exception e) {
                r.setSuccess(false);
                r.setMessage(e.getMessage() != null ? e.getMessage() : "未知错误");
                failedCount++;
                log.warn("批量重生任务 {} 失败: {}", item.getTaskId(), e.getMessage());
            }
            results.add(r);
        }
        com.dreamweaver.dto.BatchReworkResult result =
                new com.dreamweaver.dto.BatchReworkResult();
        result.setTotal(items.size());
        result.setSuccess(successCount);
        result.setFailed(failedCount);
        result.setResults(results);
        log.info("批量重生完成: total={} success={} failed={}", items.size(), successCount, failedCount);
        return result;
    }

    /** 穿帮段重生核心逻辑（单个任务），供 reworkTask 和 batchRework 共用。 */
    private TaskResponse doReworkCore(Long id, List<Integer> reworkIndices,
            Map<String, String> editedPrompts) {
        Task original = taskMapper.selectById(id);
        if (original == null) {
            throw new IllegalArgumentException("任务不存在（id=" + id + "）");
        }
        if (!"completed".equals(original.getStatus())) {
            throw new IllegalArgumentException(
                    "任务未完成（status=" + original.getStatus() + "），无法重新生成指定段");
        }
        if (original.getSegmentsJson() == null || original.getSegmentsJson().isBlank()) {
            throw new IllegalArgumentException("该任务未保存段配置，无法重新生成指定段");
        }
        if (reworkIndices == null || reworkIndices.isEmpty()) {
            throw new IllegalArgumentException("未选择需要重新生成的段");
        }

        // 1+2. 组装混合模式段配置（勾选段重生 / 未勾选段复用历史产物）。
        //      抽到 SegmentReworkPlanner 并单测 —— 这段承载过「索引错位」(949cf41) 与
        //      「缺产物的段被静默跳过」两个真实 bug，藏在 private 方法里根本测不到。
        SegmentReworkPlanner.ReworkPlan plan = reworkPlanner.plan(
                id,
                original.getSegmentsJson(),
                original.getGenType(),
                original.getResultJson(),
                original.getImageUrls(),
                reworkIndices,
                editedPrompts);
        String newSegmentsJson = plan.segmentsJson();


        // 3. 重置任务为 pending（旧产物存 prev_result_json 供回滚），段配置更新为最新版
        taskMapper.update(null, resetTaskForRerun(id, original.getResultJson())
                .set(Task::getSegmentsJson, newSegmentsJson));

        CreateTaskRequest request = new CreateTaskRequest();
        request.setPrompt(original.getPrompt());
        request.setGenType(original.getGenType());
        request.setUserId(original.getUserId() == null ? null : String.valueOf(original.getUserId()));
        request.setSegments(newSegmentsJson);
        // 全局精细控制参数还原（段级 camera/负面词已随 segments_json 落库）
        taskJsonCodec.applyGenParamsJson(original.getGenParamsJson(), request);
        log.info("重生成段: id={} 重生成段={} 复用段={}", id, plan.effectiveRework(),
                plan.reusedCount());
        return dispatchToAgent(taskMapper.selectById(id), request);
    }

    @Override
    public List<Map<String, Object>> getSegments(Long id) {
        Task task = taskMapper.selectById(id);
        if (task == null || task.getSegmentsJson() == null || task.getSegmentsJson().isBlank()) {
            return new java.util.ArrayList<>();
        }
        List<Map<String, Object>> segs = taskJsonCodec.parseSegments(task.getSegmentsJson());
        List<String> urls = taskJsonCodec.parseResultUrls(task.getResultJson());
        List<Map<String, Object>> out = new java.util.ArrayList<>();
        for (int i = 0; i < segs.size(); i++) {
            Map<String, Object> seg = new java.util.HashMap<>(segs.get(i));
            seg.put("index", i);
            seg.put("existing_video_url", i < urls.size() ? urls.get(i) : "");
            // 图片任务（文生图/漫剧）：existing_image_url 来自 image_urls 字段
            if ("text_image".equals(task.getGenType()) || "comic_video".equals(task.getGenType())) {
                List<String> imageUrls = taskJsonCodec.parseImageUrls(task.getImageUrls());
                seg.put("existing_image_url", i < imageUrls.size() ? imageUrls.get(i) : "");
            }
            // 段列表 UI 用：参考图缩略图取首张（reference_images 为 List<String>）
            List<String> refs = null;
            Object refsRaw = seg.get("reference_images");
            if (refsRaw instanceof List) {
                List<?> rawList = (List<?>) refsRaw;
                refs = rawList.stream()
                        .filter(java.util.Objects::nonNull)
                        .map(String::valueOf)
                        .collect(java.util.stream.Collectors.toList());
            }
            seg.put("thumbnail", (refs != null && !refs.isEmpty()) ? refs.get(0) : seg.get("image_url"));
            out.add(seg);
        }
        return out;
    }

    @Override
    @Transactional
    public TaskResponse setDraft(Long id, boolean isDraft) {
        Task task = taskMapper.selectById(id);
        if (task == null) {
            throw new IllegalArgumentException("任务不存在（id=" + id + "）");
        }
        // 运行中任务不应进草稿区（生成还在进行，标记无意义）
        if (!TERMINAL_STATUSES.contains(task.getStatus())) {
            throw new IllegalArgumentException("仅已终态的任务可标记草稿（当前=" + task.getStatus() + "）");
        }
        task.setIsDraft(isDraft ? 1 : 0);
        taskMapper.updateById(task);
        return toResponse(task);
    }

    private TaskResponse toResponse(Task task) {
        TaskResponse resp = new TaskResponse();
        resp.setId(task.getId());
        resp.setSessionId(task.getSessionId());
        resp.setStatus(task.getStatus());
        resp.setGenType(task.getGenType());
        resp.setResultJson(task.getResultJson());
        resp.setImageUrls(task.getImageUrls());
        resp.setSegmentsJson(task.getSegmentsJson());
        // 画廊「编辑参数」入口需要它反序列化预填历史参数
        resp.setGenParamsJson(task.getGenParamsJson());
        resp.setErrorMessage(task.getErrorMessage());
        resp.setPrompt(task.getPrompt());
        // 来源（default / canvas_asset）：前端面板要按来源分区，必须回传
        resp.setSource(task.getSource());
        // Lombok @Data 对 Boolean isDraft 生成 getIsDraft()/setIsDraft()
        resp.setIsDraft(task.getIsDraft() != null && task.getIsDraft() == 1);
        if (task.getStartedAt() != null) {
            resp.setStartedAt(task.getStartedAt().toString());
        }
        if (task.getCompletedAt() != null) {
            resp.setCompletedAt(task.getCompletedAt().toString());
        }
        if (task.getCreatedAt() != null) {
            resp.setCreatedAt(task.getCreatedAt().toString());
        }
        return resp;
    }
}