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
import java.util.List;
import java.util.Set;
import java.util.Map;
import java.util.HashMap;

/**
 * 回调处理服务实现。
 *
 * <p>幂等策略：
 * 1. 按 video_id + shot_index 组合查找任务（shot_index 可空时降级为 video_id）
 * 2. 终态检查：completed/failed 直接丢弃，防止晚到的旧回调覆盖新状态
 * 3. 状态机校验：转移表语义，只有合法边才允许跳转
 * 4. 乐观锁：通过 @Version + OptimisticLockerInnerInterceptor 自动处理，updateById 时 version+1
 * 5. result_json 聚合：按 shot_index 写入 URL 数组对应位置，不覆盖其他镜次
 *
 * <p>状态转移表（from → to）：
 * - queued → video_generating（FastAPI 开始生成，由 TaskServiceImpl 推进）
 * - video_generating → completed / failed（视频生成完成回调）
 * - queued → failed（提交失败直接回滚）
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class NotifyServiceImpl implements NotifyService {

    private final TaskMapper taskMapper;
    private final ObjectMapper objectMapper;

    /**
     * 合法状态转移表：key=from, value=Set<to>
     * Phase 1: queued → video_generating → completed/failed（理想设计）
     * Phase 1 实际: FastAPI 内联轮询，无中间 video_generating 通知
     *            → 回调直接 queued → completed/failed
     * Phase 3 扩展: 补发 video_generating 通知后可支持完整三态
     */
    private static final Map<String, Set<String>> TRANSITION_TABLE = new HashMap<>() {{
        // FastAPI 内联轮询完成 → 直接 completed/failed（Phase 1 实际路径）
        put("queued", Set.of("completed", "failed"));
        // Phase 2 异步回调预留：生成中 → 完成/失败（未来支持）
        put("video_generating", Set.of("completed", "failed"));
    }};

    @Override
    @Transactional
    public void handleCompletion(NotifyRequest request) {
        // 1. 幂等检查：按 video_id + shot_index 组合查找
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

        // 4. 状态机校验：from → to 是否在转移表内
        String fromStatus = task.getStatus();
        String toStatus = request.getStatus();
        Set<String> allowedTos = TRANSITION_TABLE.get(fromStatus);
        if (allowedTos == null || !allowedTos.contains(toStatus)) {
            log.warn("notify 任务 {} 非法状态跳转: {} → {}，丢弃回调",
                    task.getId(), fromStatus, toStatus);
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
        task.setStatus(toStatus);
        task.setUpdatedAt(LocalDateTime.now());
        if ("failed".equals(toStatus)) {
            task.setErrorMessage(request.getError_message());
        }

        int updated = taskMapper.updateById(task);
        if (updated == 0) {
            log.warn("notify 任务 {} 乐观锁冲突，丢弃（已有更新的回调处理过）", task.getId());
            return;
        }

        log.info("notify 任务 {} 状态 {} → {}，URLs 数量={}",
                task.getId(), fromStatus, toStatus, videoUrls.size());
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
