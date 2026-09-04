package com.dreamweaver.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.dreamweaver.common.CommonResult;
import com.dreamweaver.config.AgentServiceProperties;
import com.dreamweaver.dto.CreateTaskRequest;
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
    public List<TaskResponse> listTasks(int limit) {
        return taskMapper.selectList(
                new LambdaQueryWrapper<Task>()
                        .orderByDesc(Task::getId)
                        .last("LIMIT " + Math.min(Math.max(limit, 1), 50))
        ).stream().map(this::toResponse).toList();
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

        // 用原任务的 prompt + genType 提交全新任务，保留旧记录作历史
        CreateTaskRequest request = new CreateTaskRequest();
        request.setPrompt(original.getPrompt());
        request.setGenType(original.getGenType());
        request.setUserId(original.getUserId() == null ? null : String.valueOf(original.getUserId()));
        log.info("重新生成任务: 原 id={}, 新任务提交 prompt={}", id, original.getPrompt());
        return submitNewTask(request);
    }

    /**
     * 统一提交链路：落库 pending → 调 FastAPI → 回写 session_id。
     * createTask 与 regenerateTask 共用。
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

        // 2. 调 FastAPI 提交（Phase 1 同步等 session_id；后续改异步 + 回调）
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
            // FastAPI 不可达 / 排队满 429：留在 pending，由前端轮询展示
            log.warn("调 FastAPI 提交失败，任务 {} 保持 pending: {}", task.getId(), e.getMessage());
            return toResponse(task);
        }

        // 3. 回写 session_id
        if (agentResp != null && agentResp.getData() != null) {
            String sessionId = (String) agentResp.getData().get("session_id");
            task.setSessionId(sessionId);
            task.setStatus("queued");
            task.setUpdatedAt(LocalDateTime.now());
            taskMapper.updateById(task);
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
        return resp;
    }
}