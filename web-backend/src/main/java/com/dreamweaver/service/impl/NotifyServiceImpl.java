package com.dreamweaver.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.dreamweaver.dto.NotifyRequest;
import com.dreamweaver.entity.Task;
import com.dreamweaver.mapper.TaskMapper;
import com.dreamweaver.service.NotifyService;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * 回调处理服务实现。
 *
 * <p>幂等策略：
 * 1. 按 video_id + shot_index 组合查找任务（shot_index 可空时降级为 video_id）
 * 2. 终态检查：completed/failed 直接丢弃，防止晚到的旧回调覆盖新状态
 * 3. 乐观锁：通过 @Version + OptimisticLockerInnerInterceptor 自动处理，updateById 时 version+1
 * 4. 状态机校验：只允许 video_generating → completed/failed 跳转
 * 5. result_json 聚合：按 shot_index 写入 URL 数组对应位置，不覆盖其他镜次
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class NotifyServiceImpl implements NotifyService {

    private final TaskMapper taskMapper;
    private final ObjectMapper objectMapper;

    /** 允许的状态跳转：只有 video_generating 可以转为 completed/failed */
    private static final String GENERATING_STATUS = "video_generating";
    private static final List<String> ALLOWED_FROM_STATUSES = List.of(GENERATING_STATUS);

    @Override
    @Transactional
    public void handleCompletion(NotifyRequest request) {
        // 1. 幂等检查：按 video_id + shot_index 组合查找
        // shot_index 可空时降级为只按 video_id（用 selectList 兜底 TooManyResultsException）
        List<Task> tasks = taskMapper.selectList(
            new LambdaQueryWrapper<Task>()
                .eq(Task::getVideoId, request.getVideo_id())
                .eq(request.getShot_index() != null, Task::getShotIndex, request.getShot_index())
        );

        if (tasks.isEmpty()) {
            log.warn("notify 收到未知任务: video_id={}, shot_index={}",
                    request.getVideo_id(), request.getShot_index());
            return;
        }

        // 2. 优先按 shot_index 精确匹配，否则取第一条
        Task task = tasks.stream()
                .filter(t -> request.getShot_index() != null &&
                        request.getShot_index().equals(t.getShotIndex()))
                .findFirst()
                .orElse(tasks.get(0));

        // 3. 终态检查：completed / failed 直接丢弃
        if ("completed".equals(task.getStatus()) || "failed".equals(task.getStatus())) {
            log.info("notify 任务 {} 已终态 (status={})，丢弃回调",
                    task.getId(), task.getStatus());
            return;
        }

        // 4. 状态机校验：只允许从生成中状态跳转
        if (!ALLOWED_FROM_STATUSES.contains(task.getStatus())) {
            log.warn("notify 任务 {} 状态非法 (current={})，丢弃回调",
                    task.getId(), task.getStatus());
            return;
        }

        // 5. 聚合 result_json：读出旧值，按 shot_index 写入，避免多镜覆盖
        List<String> videoUrls = parseOrEmptyUrls(task.getResultJson());
        int idx = request.getShot_index() != null ? request.getShot_index() : videoUrls.size();
        while (videoUrls.size() <= idx) {
            videoUrls.add(null);
        }
        videoUrls.set(idx, request.getVideo_url());
        task.setResultJson(toJsonString(videoUrls));

        // 6. 更新状态（通过 updateById 触发乐观锁 version+1）
        task.setStatus(request.getStatus());
        task.setUpdatedAt(LocalDateTime.now());
        if ("failed".equals(request.getStatus())) {
            task.setErrorMessage(request.getError_message());
        }

        int updated = taskMapper.updateById(task);
        if (updated == 0) {
            log.warn("notify 任务 {} 乐观锁冲突，丢弃（已有更新的回调处理过）", task.getId());
            return;
        }

        log.info("notify 任务 {} 处理完成，状态={}，URLs 数量={}",
                task.getId(), request.getStatus(), videoUrls.size());
    }

    @SuppressWarnings("unchecked")
    private List<String> parseOrEmptyUrls(String json) {
        if (json == null || json.isBlank()) {
            return new ArrayList<>();
        }
        try {
            List<String> list = objectMapper.readValue(json, new TypeReference<List<String>>() {});
            return list != null ? list : new ArrayList<>();
        } catch (Exception e) {
            log.warn("解析 resultJson 失败: {}", json, e);
            return new ArrayList<>();
        }
    }

    private String toJsonString(List<String> list) {
        try {
            return objectMapper.writeValueAsString(list);
        } catch (Exception e) {
            log.error("序列化 videoUrls 失败", e);
            return "[]";
        }
    }
}
