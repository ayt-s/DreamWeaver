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
    public TaskListResponse listTasks(int page, int size, String genType) {
        int safePage = Math.max(page, 1);
        int safeSize = Math.min(Math.max(size, 1), 50);
        boolean hasFilter = genType != null && !genType.isBlank();
        long total = taskMapper.selectCount(
                new LambdaQueryWrapper<Task>()
                        .eq(hasFilter, Task::getGenType, genType)
        );
        List<TaskResponse> list = taskMapper.selectList(
                new LambdaQueryWrapper<Task>()
                        .eq(hasFilter, Task::getGenType, genType)
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
        original.setStatus("pending");
        original.setSessionId(null);
        original.setResultJson(null);
        original.setImageUrls(null);
        original.setErrorMessage(null);
        original.setUpdatedAt(LocalDateTime.now());
        taskMapper.updateById(original);

        CreateTaskRequest request = new CreateTaskRequest();
        request.setPrompt(original.getPrompt());
        request.setGenType(original.getGenType());
        request.setUserId(original.getUserId() == null ? null : String.valueOf(original.getUserId()));
        log.info("重新生成任务: id={} 原地重跑 prompt={}", id, original.getPrompt());
        return dispatchToAgent(original, request);
    }

    /**
     * 统一提交链路（新建）：落库 pending → 调 FastAPI → 回写 session_id。
     */
    private TaskResponse submitNewTask(CreateTaskRequest request) {
        // 1. 落库（pending）
        Task task = new Task();
        task.setPrompt(request.getPrompt());
        task.setUserId(request.getUserId() == null ? null : Long.valueOf(request.getUserId()));
        task.setStatus("pending");
        task.setGenType(request.getGenType() != null ? request.getGenType() : "text_video");
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
            // FastAPI 不可达：如实落 failed，前端可删除/重新提交；否则任务会静默卡 pending
            log.warn("调 FastAPI 提交失败，任务 {} 转 failed: {}", task.getId(), e.getMessage());
            task.setStatus("failed");
            task.setErrorMessage("Agent 服务提交失败: " + e.getMessage());
            task.setUpdatedAt(LocalDateTime.now());
            taskMapper.updateById(task);
            return toResponse(task);
        }

        // 3. 回写 session_id
        if (agentResp != null && agentResp.getData() != null) {
            String sessionId = (String) agentResp.getData().get("session_id");
            task.setSessionId(sessionId);
            task.setStatus("queued");
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

    private TaskResponse toResponse(Task task) {
        TaskResponse resp = new TaskResponse();
        resp.setId(task.getId());
        resp.setSessionId(task.getSessionId());
        resp.setStatus(task.getStatus());
        resp.setGenType(task.getGenType());
        resp.setResultJson(task.getResultJson());
        resp.setImageUrls(task.getImageUrls());
        resp.setErrorMessage(task.getErrorMessage());
        resp.setPrompt(task.getPrompt());
        return resp;
    }
}