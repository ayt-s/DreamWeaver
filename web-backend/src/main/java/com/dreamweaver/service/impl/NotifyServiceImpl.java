package com.dreamweaver.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.dreamweaver.dto.NotifyRequest;
import com.dreamweaver.entity.Task;
import com.dreamweaver.mapper.ApiQuotaMapper;
import com.dreamweaver.mapper.TaskMapper;
import com.dreamweaver.service.NotifyService;
import com.fasterxml.jackson.databind.ObjectMapper;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Set;
import java.util.Map;
import java.util.HashMap;

/**
 * 回调处理服务实现。
 *
 * <p>幂等策略：
 * 1. 按 session_id 关联任务（Java 侧无 video_id 列，2026-09 契约修复）
 * 2. 终态检查：completed/failed 直接丢弃，防止晚到的旧回调覆盖新状态
 * 3. 状态机校验：转移表语义，只有合法边才允许跳转
 * 4. 乐观锁：通过 @Version + OptimisticLockerInnerInterceptor 自动处理，updateById 时 version+1
 * 5. result_json 聚合：整会话回调携带全量 URL 数组，直接写入（不再逐镜覆盖）
 * 6. 配额累加：回调完成后，按 userId + model_name 累加 used_count 与 used_seconds
 *
 * <p>状态转移表（from → to）：
 * - queued → completed / failed（Phase 1 实际路径：FastAPI 内联轮询完成后整会话回调一次）
 * - video_generating → completed / failed（Phase 2 异步回调预留：补发生成中通知后支持三态）
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class NotifyServiceImpl implements NotifyService {

    private final TaskMapper taskMapper;
    private final ApiQuotaMapper apiQuotaMapper;
    private final ObjectMapper objectMapper;
    private final StuckTaskWatchdog stuckTaskWatchdog;
    private final ImageCacheService imageCacheService;

    /** 默认单镜时长（秒），当回调未携带 shot_seconds 时使用 */
    private static final int DEFAULT_SHOT_SECONDS = 5;

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
        // 1. 按 session_id 查找任务（Java 侧无 video_id 列，这是唯一回写过的关联键）
        if (request.getSession_id() == null || request.getSession_id().isBlank()) {
            log.warn("notify 缺少 session_id，丢弃回调");
            return;
        }
        List<Task> tasks = taskMapper.selectList(
            new LambdaQueryWrapper<Task>()
                .eq(Task::getSessionId, request.getSession_id())
        );

        if (tasks.isEmpty()) {
            log.warn("notify 收到未知任务: session_id={}", request.getSession_id());
            return;
        }

        // session_id 理论上唯一，取第一条
        Task task = tasks.get(0);

        // 2. 终态检查：completed / failed 直接丢弃
        if ("completed".equals(task.getStatus()) || "failed".equals(task.getStatus())) {
            log.info("notify 任务 {} 已终态 (status={})，丢弃回调",
                    task.getId(), task.getStatus());
            return;
        }

        // 3. 状态机校验：from → to 是否在转移表内
        String fromStatus = task.getStatus();
        String toStatus = request.getStatus();
        Set<String> allowedTos = TRANSITION_TABLE.get(fromStatus);
        if (allowedTos == null || !allowedTos.contains(toStatus)) {
            log.warn("notify 任务 {} 非法状态跳转: {} → {}，丢弃回调",
                    task.getId(), fromStatus, toStatus);
            return;
        }

        // 4. 聚合 result_json：整会话回调携带全量 URL 数组（主载荷）
        //    兼容旧单值 video_url 回调（Phase 1 早前版本）
        List<String> videoUrls = request.getVideo_urls();
        if ((videoUrls == null || videoUrls.isEmpty()) && request.getVideo_url() != null) {
            videoUrls = List.of(request.getVideo_url());
        }
        if (videoUrls == null) {
            videoUrls = List.of();
        }
        task.setResultJson(toJsonString(videoUrls));

        // 4.1 图片资产落库（文生图产物）
        List<String> imageUrls = request.getImage_urls();
        if (imageUrls != null && !imageUrls.isEmpty()) {
            task.setImageUrls(toJsonString(imageUrls));
        }

        // 5. 更新状态（通过 updateById 触发乐观锁 version+1）
        task.setStatus(toStatus);
        task.setUpdatedAt(LocalDateTime.now());
        int updated;
        if ("failed".equals(toStatus)) {
            task.setErrorMessage(request.getError_message());
            updated = taskMapper.updateById(task);
        } else {
            // completed：显式清空 errorMessage（updateById 忽略 null，走 wrapper 直写 + version+1）
            updated = taskMapper.update(null, new com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper<com.dreamweaver.entity.Task>()
                    .eq(com.dreamweaver.entity.Task::getId, task.getId())
                    .eq(com.dreamweaver.entity.Task::getVersion, task.getVersion())
                    .set(com.dreamweaver.entity.Task::getStatus, "completed")
                    .set(com.dreamweaver.entity.Task::getResultJson, task.getResultJson())
                    .set(com.dreamweaver.entity.Task::getImageUrls, task.getImageUrls())
                    .set(com.dreamweaver.entity.Task::getErrorMessage, null)
                    .set(com.dreamweaver.entity.Task::getUpdatedAt, LocalDateTime.now())
                    .setSql("version = version + 1"));
        }
        if (updated == 0) {
            log.warn("notify 任务 {} 乐观锁冲突，丢弃（已有更新的回调处理过）", task.getId());
            return;
        }
        // 已闭环：解除 Redis 看门狗
        stuckTaskWatchdog.clear(task.getId());
        // 预取产物图到 Redis 缓存（异步，失败静默；画廊展示不再等 agnes CDN）
        if (imageUrls != null) {
            for (String imageUrl : imageUrls) {
                if (imageUrl != null && !imageUrl.isBlank()) {
                    String finalUrl = imageUrl;
                    java.util.concurrent.CompletableFuture.runAsync(() -> imageCacheService.warm(finalUrl));
                }
            }
        }

        log.info("notify 任务 {} 状态 {} → {}，URLs 数量={}",
                task.getId(), fromStatus, toStatus, videoUrls.size());

        // 6. 配额累加
        if (task.getUserId() != null) {
            int shotSeconds = request.getShot_seconds() != null ? request.getShot_seconds() : DEFAULT_SHOT_SECONDS;
            // model_name 暂取默认值，后续可从任务或配置中获取
            String modelName = "default";
            apiQuotaMapper.increment(task.getUserId(), modelName, 1, shotSeconds);
            log.info("notify 任务 {} 配额累加: userId={}, model={}, +count=1, +seconds={}",
                    task.getId(), task.getUserId(), modelName, shotSeconds);
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
