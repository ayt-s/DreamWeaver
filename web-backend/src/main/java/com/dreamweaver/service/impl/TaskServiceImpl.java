package com.dreamweaver.service.impl;

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
import java.util.Map;

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

        // 2. 调 FastAPI 提交（Phase 1 同步等 session_id；后续改异步 + 回调）
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
                .block();

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

    @Override
    public TaskResponse getTask(Long id) {
        Task task = taskMapper.selectById(id);
        return task == null ? null : toResponse(task);
    }

    private TaskResponse toResponse(Task task) {
        TaskResponse resp = new TaskResponse();
        resp.setId(task.getId());
        resp.setSessionId(task.getSessionId());
        resp.setStatus(task.getStatus());
        resp.setErrorMessage(task.getErrorMessage());
        return resp;
    }
}