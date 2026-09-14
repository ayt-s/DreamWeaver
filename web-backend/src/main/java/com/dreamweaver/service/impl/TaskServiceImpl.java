package com.dreamweaver.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.dreamweaver.common.CommonResult;
import com.dreamweaver.config.AgentServiceProperties;
import com.dreamweaver.dto.CreateTaskRequest;
import com.dreamweaver.dto.TaskListResponse;
import com.dreamweaver.dto.TaskResponse;
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

    private final TaskMapper taskMapper;
    private final AgentServiceProperties agentServiceProperties;
    private final WebClient.Builder webClientBuilder;
    private final StuckTaskWatchdog stuckTaskWatchdog;
    private final com.fasterxml.jackson.databind.ObjectMapper objectMapper;

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

    @Override
    public TaskListResponse listTasks(int page, int size, String genType, Boolean draft) {
        int safePage = Math.max(page, 1);
        int safeSize = Math.min(Math.max(size, 1), 50);
        boolean hasTypeFilter = genType != null && !genType.isBlank();
        // draft 为 null = 不按草稿筛选；true/false = 只取草稿/只取成品
        boolean hasDraftFilter = draft != null;
        int draftFlag = draft != null && draft ? 1 : 0;
        long total = taskMapper.selectCount(
                new LambdaQueryWrapper<Task>()
                        .eq(hasTypeFilter, Task::getGenType, genType)
                        .eq(hasDraftFilter, Task::getIsDraft, draftFlag)
        );
        List<TaskResponse> list = taskMapper.selectList(
                new LambdaQueryWrapper<Task>()
                        .eq(hasTypeFilter, Task::getGenType, genType)
                        .eq(hasDraftFilter, Task::getIsDraft, draftFlag)
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

    @Override
    @Transactional
    public TaskResponse regenerateTask(Long id) {
        Task original = taskMapper.selectById(id);
        if (original == null) {
            throw new IllegalArgumentException("任务不存在（id=" + id + "）");
        }
        if (!TERMINAL_STATUSES.contains(original.getStatus())) {
            throw new IllegalArgumentException(
                    "任务正在生成中（status=" + original.getStatus() + "），无法重新生成");
        }

        // 同一任务原地重新生成：清空旧产物与错误，保留 id/prompt/genType/userId，
        // 重新走 提交→排队→生成→回调 链路（不再创建新任务 id）
        // 注意：updateById 的 FieldStrategy.NOT_NULL 会忽略 null 字段，显式置空必须走 wrapper
        taskMapper.update(null, new com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper<com.dreamweaver.entity.Task>()
                .eq(com.dreamweaver.entity.Task::getId, id)
                .set(com.dreamweaver.entity.Task::getStatus, "pending")
                .set(com.dreamweaver.entity.Task::getSessionId, null)
                .set(com.dreamweaver.entity.Task::getResultJson, null)
                .set(com.dreamweaver.entity.Task::getImageUrls, null)
                .set(com.dreamweaver.entity.Task::getErrorMessage, null)
                .set(com.dreamweaver.entity.Task::getCompletedAt, null)
                // 全量重生成 → 旧产物存 prev_result_json 供回滚，段配置失效一并清空
                .set(com.dreamweaver.entity.Task::getPrevResultJson, original.getResultJson())
                .set(com.dreamweaver.entity.Task::getSegmentsJson, null)
                .set(com.dreamweaver.entity.Task::getUpdatedAt, LocalDateTime.now()));

        CreateTaskRequest request = new CreateTaskRequest();
        request.setPrompt(original.getPrompt());
        request.setGenType(original.getGenType());
        request.setUserId(original.getUserId() == null ? null : String.valueOf(original.getUserId()));
        // 还原精细控制参数（风格/负面词/时间轴/元素绑定），否则重生成会丢设定
        applyGenParamsJson(original.getGenParamsJson(), request);
        log.info("重新生成任务: id={} 原地重跑 prompt={}", id, original.getPrompt());
        return dispatchToAgent(original, request);
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
                    new com.fasterxml.jackson.databind.ObjectMapper().readValue(
                            segmentsJson, new com.fasterxml.jackson.core.type.TypeReference<java.util.List<java.util.Map<String, Object>>>() {});
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
        task.setUserId(request.getUserId() == null || request.getUserId().isBlank() ? null : Long.valueOf(request.getUserId()));
        task.setStatus("pending");
        task.setGenType(request.getGenType() != null ? request.getGenType() : "text_video");
        // 段配置落库：重生时取此作为输入源（未勾选段复用已有视频、勾选段重新生成）
        task.setSegmentsJson(request.getSegments());
        // 精细控制参数落库：regenerate 从 entity 重建请求时需要还原
        task.setGenParamsJson(buildGenParamsJson(request));
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

        // 1. 解析段配置 + 已有产物 URL
        List<Map<String, Object>> segs = parseSegments(original.getSegmentsJson());
        boolean isImageTask = "text_image".equals(original.getGenType())
                || "comic_video".equals(original.getGenType());
        List<String> existingUrls = isImageTask
                ? parseImageUrls(original.getImageUrls())
                : parseResultUrls(original.getResultJson());
        if (existingUrls.size() != segs.size()) {
            throw new IllegalArgumentException(
                    "历史结果与段数不匹配（段数=" + segs.size()
                            + "，已有" + (isImageTask ? "图片" : "视频") + "=" + existingUrls.size()
                            + "），存在历史段生成失败导致序号错位，无法按段重生，请全量重新生成");
        }

        // 2. 组装混合模式段：未勾选复用 existing_video_url（视频）/ existing_image_url（图片），勾选段更新 prompt 后重新生成
        List<String> existingImageUrls = parseImageUrls(original.getImageUrls());
        Set<Integer> reworkSet = new java.util.HashSet<>(reworkIndices);
        List<Map<String, Object>> out = new java.util.ArrayList<>();
        for (int i = 0; i < segs.size(); i++) {
            Map<String, Object> seg = new java.util.HashMap<>(segs.get(i));
            if (reworkSet.contains(i)) {
                String edited = (editedPrompts != null)
                        ? editedPrompts.get(String.valueOf(i)) : null;
                if (edited != null && !edited.isBlank()) {
                    seg.put("prompt", edited);
                }
                // 重活段必须重新翻译（旧 prompt_en 对应用户修改前的中文描述）
                seg.remove("prompt_en");
                seg.remove("existing_video_url");
                seg.remove("existing_image_url");
            } else if (isImageTask && i < existingImageUrls.size()) {
                seg.put("existing_image_url", existingImageUrls.get(i));
            } else {
                seg.put("existing_video_url", existingUrls.get(i));
            }
            out.add(seg);
        }
        String newSegmentsJson = toJsonString(out);

        // 3. 重置任务为 pending（旧产物存 prev_result_json 供回滚），段配置更新为最新版
        taskMapper.update(null, new com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper<com.dreamweaver.entity.Task>()
                .eq(com.dreamweaver.entity.Task::getId, id)
                .set(com.dreamweaver.entity.Task::getStatus, "pending")
                .set(com.dreamweaver.entity.Task::getSessionId, null)
                .set(com.dreamweaver.entity.Task::getResultJson, null)
                .set(com.dreamweaver.entity.Task::getErrorMessage, null)
                .set(com.dreamweaver.entity.Task::getCompletedAt, null)
                .set(com.dreamweaver.entity.Task::getPrevResultJson, original.getResultJson())
                .set(com.dreamweaver.entity.Task::getSegmentsJson, newSegmentsJson)
                .set(com.dreamweaver.entity.Task::getUpdatedAt, LocalDateTime.now()));

        CreateTaskRequest request = new CreateTaskRequest();
        request.setPrompt(original.getPrompt());
        request.setGenType(original.getGenType());
        request.setUserId(original.getUserId() == null ? null : String.valueOf(original.getUserId()));
        request.setSegments(newSegmentsJson);
        // 全局精细控制参数还原（段级 camera/负面词已随 segments_json 落库）
        applyGenParamsJson(original.getGenParamsJson(), request);
        log.info("重生成段: id={} 重生成段={} 复用段={}", id, reworkIndices, segs.size() - reworkSet.size());
        return dispatchToAgent(taskMapper.selectById(id), request);
    }

    @Override
    public List<Map<String, Object>> getSegments(Long id) {
        Task task = taskMapper.selectById(id);
        if (task == null || task.getSegmentsJson() == null || task.getSegmentsJson().isBlank()) {
            return new java.util.ArrayList<>();
        }
        List<Map<String, Object>> segs = parseSegments(task.getSegmentsJson());
        List<String> urls = parseResultUrls(task.getResultJson());
        List<Map<String, Object>> out = new java.util.ArrayList<>();
        for (int i = 0; i < segs.size(); i++) {
            Map<String, Object> seg = new java.util.HashMap<>(segs.get(i));
            seg.put("index", i);
            seg.put("existing_video_url", i < urls.size() ? urls.get(i) : "");
            // 图片任务（文生图/漫剧）：existing_image_url 来自 image_urls 字段
            if ("text_image".equals(task.getGenType()) || "comic_video".equals(task.getGenType())) {
                List<String> imageUrls = parseImageUrls(task.getImageUrls());
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

    /** 解析提交时落库的段配置 JSON 数组 */
    private List<Map<String, Object>> parseSegments(String json) {
        try {
            return objectMapper.readValue(json,
                    new com.fasterxml.jackson.core.type.TypeReference<List<Map<String, Object>>>() {});
        } catch (Exception e) {
            log.warn("解析 segments_json 失败: {}", e.getMessage());
            return new java.util.ArrayList<>();
        }
    }

    /**
     * 把可灵式精细控制参数序列化为 JSON 落库。
     * 全空时返回 null（不写无意义的 {} 占位，便于判断「是否配置过」）。
     */
    private String buildGenParamsJson(CreateTaskRequest request) {
        boolean empty = (request.getStylePrompt() == null || request.getStylePrompt().isBlank())
                && (request.getNegativePrompt() == null || request.getNegativePrompt().isBlank())
                && request.getTotalSeconds() == null
                && request.getShotCount() == null
                && (request.getReferenceBindings() == null || request.getReferenceBindings().isBlank());
        if (empty) {
            return null;
        }
        Map<String, Object> params = new java.util.LinkedHashMap<>();
        params.put("stylePrompt", request.getStylePrompt());
        params.put("negativePrompt", request.getNegativePrompt());
        params.put("totalSeconds", request.getTotalSeconds());
        params.put("shotCount", request.getShotCount());
        params.put("referenceBindings", request.getReferenceBindings());
        try {
            return objectMapper.writeValueAsString(params);
        } catch (Exception e) {
            log.warn("序列化 gen_params_json 失败: {}", e.getMessage());
            return null;
        }
    }

    /** 从落库的 gen_params_json 还原精细控制参数到请求体（regenerate / rework 共用）。 */
    private void applyGenParamsJson(String genParamsJson, CreateTaskRequest request) {
        if (genParamsJson == null || genParamsJson.isBlank()) {
            return;
        }
        try {
            Map<String, Object> params = objectMapper.readValue(genParamsJson,
                    new com.fasterxml.jackson.core.type.TypeReference<Map<String, Object>>() {});
            request.setStylePrompt(asText(params.get("stylePrompt")));
            request.setNegativePrompt(asText(params.get("negativePrompt")));
            request.setTotalSeconds(asInt(params.get("totalSeconds")));
            request.setShotCount(asInt(params.get("shotCount")));
            request.setReferenceBindings(asText(params.get("referenceBindings")));
        } catch (Exception e) {
            log.warn("解析 gen_params_json 失败: {}", e.getMessage());
        }
    }

    private static String asText(Object value) {
        return value == null ? null : String.valueOf(value);
    }

    private static Integer asInt(Object value) {
        if (value == null) {
            return null;
        }
        if (value instanceof Number) {
            return ((Number) value).intValue();
        }
        try {
            return Integer.valueOf(String.valueOf(value).trim());
        } catch (NumberFormatException e) {
            return null;
        }
    }

    /** 解析 result_json 为各分段视频 URL。
     *
     * 格式有两种：
     * - 拼接成功：[final.mp4, seg0, seg1, ...]（首元素是本地成片 /v1/files/**，丢弃）
     * - 拼接失败：[seg0, seg1, ...]（synthesizer 兜底透传，无成片，全部保留）
     * 判定依据与前端 finalVideoUrl 一致：仅本地静态目录路径才算成片。
     */
    private List<String> parseResultUrls(String json) {
        if (json == null || json.isBlank()) {
            return new java.util.ArrayList<>();
        }
        try {
            List<String> urls = objectMapper.readValue(json,
                    new com.fasterxml.jackson.core.type.TypeReference<List<String>>() {});
            if (urls.isEmpty()) {
                return new java.util.ArrayList<>();
            }
            String first = urls.get(0) == null ? "" : urls.get(0).trim();
            boolean hasFinalVideo = first.startsWith("/v1/files/") || first.endsWith("/final.mp4");
            if (hasFinalVideo && urls.size() > 1) {
                // 有拼接成片：首元素是成片，丢弃它，只返回分段视频
                return new java.util.ArrayList<>(urls.subList(1, urls.size()));
            }
            // 拼接失败：首元素也是分段视频，全部返回
            return new java.util.ArrayList<>(urls);
        } catch (Exception e) {
            log.warn("解析 result_json 失败: {}", e.getMessage());
            return new java.util.ArrayList<>();
        }
    }

    /** 解析 image_urls JSON 为图片 URL 列表（容错同 parseResultUrls） */
    private List<String> parseImageUrls(String json) {
        if (json == null || json.isBlank()) {
            return new java.util.ArrayList<>();
        }
        try {
            List<String> urls = objectMapper.readValue(json,
                    new com.fasterxml.jackson.core.type.TypeReference<List<String>>() {});
            return new java.util.ArrayList<>(urls);
        } catch (Exception e) {
            log.warn("解析 image_urls 失败: {}", e.getMessage());
            return new java.util.ArrayList<>();
        }
    }

    /** JSON 序列化（段配置数组落库用）；失败返回空数组字符串，避免阻断提交 */
    private String toJsonString(java.util.List<?> list) {
        try {
            return objectMapper.writeValueAsString(list);
        } catch (Exception e) {
            log.error("序列化段配置失败", e);
            return "[]";
        }
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
        resp.setErrorMessage(task.getErrorMessage());
        resp.setPrompt(task.getPrompt());
        // Lombok @Data 对 Boolean isDraft 生成 getIsDraft()/setIsDraft()
        resp.setIsDraft(task.getIsDraft() != null && task.getIsDraft() == 1);
        if (task.getCompletedAt() != null) {
            resp.setCompletedAt(task.getCompletedAt().toString());
        }
        if (task.getCreatedAt() != null) {
            resp.setCreatedAt(task.getCreatedAt().toString());
        }
        return resp;
    }
}