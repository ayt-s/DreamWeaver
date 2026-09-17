package com.dreamweaver.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.dreamweaver.dto.HeartbeatRequest;
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
 *    （interrupted 不是终态：Agent 重启恢复后补发的迟到回调必须能落定）
 * 3. 状态机校验：转移表语义，只有合法边才允许跳转
 * 4. 乐观锁：通过 @Version + OptimisticLockerInnerInterceptor 自动处理，updateById 时 version+1
 * 5. result_json 聚合：整会话回调携带全量 URL 数组，直接写入（不再逐镜覆盖）
 * 6. 配额累加：回调完成后，按 userId + model_name 累加 used_count 与 used_seconds
 *
 * <p>状态转移表（from → to）：
 * - queued → completed / failed / interrupted（Phase 1 实际路径：FastAPI 内联轮询完成后整会话回调一次）
 * - video_generating → completed / failed（Phase 2 异步回调预留：补发生成中通知后支持三态）
 * - interrupted → completed / failed / queued（Agent 重启恢复：迟到回调落定 或 重新报到回退排队）
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
        // queued → interrupted：Agent 长时间未回调被看门狗兜底转中断（非终态）
        put("queued", Set.of("completed", "failed", "interrupted"));
        // Phase 2 异步回调预留：生成中 → 完成/失败（未来支持）
        put("video_generating", Set.of("completed", "failed"));
        // Agent 重启恢复：中断态收到补发的迟到回调 → 落定；恢复后重新报到 → 回退排队
        put("interrupted", Set.of("completed", "failed", "queued"));
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

        // 2. 终态检查：completed / failed 直接丢弃（幂等，防晚到旧回调覆盖新状态）
        //    interrupted 不算终态 → Agent 重启恢复后补发的迟到 completed 回调可以走到下面的状态机
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

        // 4.2 保存 storyboard 为 segments_json（标准模式分镜 / 文生图分镜，供段重生用）
        if (request.getStoryboard() != null && !request.getStoryboard().isBlank()
                && (task.getSegmentsJson() == null || task.getSegmentsJson().isBlank())) {
            task.setSegmentsJson(request.getStoryboard());
            log.info("notify 任务 {} 保存 storyboard 为 segments_json（长度={}）",
                    task.getId(), request.getStoryboard().length());
        }

        // 5. 更新状态（通过 updateById 触发乐观锁 version+1）
        task.setStatus(toStatus);
        task.setUpdatedAt(LocalDateTime.now());
        int updated;
        if ("failed".equals(toStatus)) {
            // ⚠️ 必须显式 `.set(...)`，**不能**沿用 updateById —— MyBatis-Plus 默认
            //    `FieldStrategy.NOT_NULL` 会**跳过 null 字段**。后果不是"少写一个字段"
            //    这么轻：agent 报 failed 但没带 error_message 时，库里上一轮的旧原因
            //    （例如回退排队时写的「Agent 重启恢复」）会原样留着 ——
            //    任务卡片上显示的是一个**与本次失败无关的假原因**，排查时会被它带偏。
            //    本文件另外三个分支都是显式 set，只有这里靠 NOT_NULL 兜底（唯一的不对称）。
            String warn = request.getError_message();
            String errorToStore = (warn != null && !warn.isBlank()) ? warn : null;
            updated = taskMapper.update(null, new com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper<com.dreamweaver.entity.Task>()
                    .eq(com.dreamweaver.entity.Task::getId, task.getId())
                    .eq(com.dreamweaver.entity.Task::getVersion, task.getVersion())
                    .set(com.dreamweaver.entity.Task::getStatus, "failed")
                    // 本轮已聚合的产物字段照样写回（保持与 completed 分支同口径）
                    .set(com.dreamweaver.entity.Task::getResultJson, task.getResultJson())
                    .set(com.dreamweaver.entity.Task::getImageUrls, task.getImageUrls())
                    .set(com.dreamweaver.entity.Task::getSegmentsJson, task.getSegmentsJson())
                    // null 也要显式清 —— 这正是 updateById 做不到的那一步
                    .set(com.dreamweaver.entity.Task::getErrorMessage, errorToStore)
                    .set(com.dreamweaver.entity.Task::getCompletedAt, LocalDateTime.now())
                    .set(com.dreamweaver.entity.Task::getUpdatedAt, LocalDateTime.now())
                    .setSql("version = version + 1"));
        } else if ("queued".equals(toStatus) || "interrupted".equals(toStatus)) {
            // Agent 重启恢复后回退报到（interrupted → queued）或显式报中断：
            // 非终态回退，不写 completed_at、不覆盖已聚合的产物字段，只回写状态与提示。
            // 两个时间戳都必须显式清空：过去只「不写」completed_at，导致 failed 写过的
            // 旧值残留（实测 id=36：status=queued 却带着 completed_at=09-14 23:42:22，
            // 画廊显示「耗时 2 秒 · 排队中」自相矛盾）；started_at 一并清零，等重跑重新打点。
            String warn = request.getError_message();
            String errorToStore = (warn != null && !warn.isBlank()) ? warn : null;
            updated = taskMapper.update(null, new com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper<com.dreamweaver.entity.Task>()
                    .eq(com.dreamweaver.entity.Task::getId, task.getId())
                    .eq(com.dreamweaver.entity.Task::getVersion, task.getVersion())
                    .set(com.dreamweaver.entity.Task::getStatus, toStatus)
                    .set(com.dreamweaver.entity.Task::getCompletedAt, null)
                    .set(com.dreamweaver.entity.Task::getStartedAt, null)
                    .set(com.dreamweaver.entity.Task::getErrorMessage, errorToStore)
                    .set(com.dreamweaver.entity.Task::getUpdatedAt, LocalDateTime.now())
                    .setSql("version = version + 1"));
        } else {
            // completed：正常情况下显式清空 errorMessage（避免历史错误残留）；
            // 但 Agent 显式带回的警告（如「多镜拼接失败，分段仍可下载」）必须保留并展示，
            // 否则任务显示 completed 却没有成片，用户无从判断（实测故障：静默降级）。
            String warn = request.getError_message();
            String errorToStore = (warn != null && !warn.isBlank()) ? warn : null;
            updated = taskMapper.update(null, new com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper<com.dreamweaver.entity.Task>()
                    .eq(com.dreamweaver.entity.Task::getId, task.getId())
                    .eq(com.dreamweaver.entity.Task::getVersion, task.getVersion())
                    .set(com.dreamweaver.entity.Task::getStatus, "completed")
                    .set(com.dreamweaver.entity.Task::getResultJson, task.getResultJson())
                    .set(com.dreamweaver.entity.Task::getImageUrls, task.getImageUrls())
                    .set(com.dreamweaver.entity.Task::getSegmentsJson, task.getSegmentsJson())
                    .set(com.dreamweaver.entity.Task::getErrorMessage, errorToStore)
                    .set(com.dreamweaver.entity.Task::getCompletedAt, LocalDateTime.now())
                    .set(com.dreamweaver.entity.Task::getUpdatedAt, LocalDateTime.now())
                    .setSql("version = version + 1"));
        }
        if (updated == 0) {
            log.warn("notify 任务 {} 乐观锁冲突，丢弃（已有更新的回调处理过）", task.getId());
            return;
        }
        // 已闭环：解除 Redis 看门狗
        // 但回退到 queued（Agent 恢复后重新报到）时改为重新武装，防止恢复过程中 Agent 再次挂掉无人兜底
        if ("queued".equals(toStatus)) {
            stuckTaskWatchdog.watch(task.getId(), task.getGenType());
        } else {
            stuckTaskWatchdog.clear(task.getId());
        }
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

        // 6. 配额累加（仅终态计费：queued/interrupted 回退报到不是新的一次生成，避免重复累加）
        if (task.getUserId() != null && !"queued".equals(toStatus) && !"interrupted".equals(toStatus)) {
            int shotSeconds = request.getShot_seconds() != null ? request.getShot_seconds() : DEFAULT_SHOT_SECONDS;
            // model_name 暂取默认值，后续可从任务或配置中获取
            String modelName = "default";
            apiQuotaMapper.increment(task.getUserId(), modelName, 1, shotSeconds);
            log.info("notify 任务 {} 配额累加: userId={}, model={}, +count=1, +seconds={}",
                    task.getId(), task.getUserId(), modelName, shotSeconds);
        }
    }

    /**
     * Agent 心跳续期：长任务仍在生成时 Agent 周期性报到，按 session_id 重新武装看门狗 TTL，
     * 使任务不再被固定超时误杀（仅靠提交时武装一次，超出 TTL 会被兜底成 interrupted）。
     *
     * <p>刻意保持安静：session_id 缺失/任务不存在/任务已终态都直接返回，
     * 心跳失败不能让 Agent 侧生成流程报错（Agent 不关心响应体）。
     * 非事务：只做一次查询 + 一次 Redis 写。
     */
    @Override
    public Map<String, Object> handleHeartbeat(HeartbeatRequest request) {
        String sessionId = (request == null) ? null : request.getSession_id();
        if (sessionId == null || sessionId.isBlank()) {
            return Map.of("tracked", false);
        }
        // 与 handleCompletion 同一关联键查法（Java 侧无 video_id 列）
        List<Task> tasks = taskMapper.selectList(
            new LambdaQueryWrapper<Task>()
                .eq(Task::getSessionId, sessionId)
        );
        if (tasks.isEmpty()) {
            // 任务查不到 = 已被删除，或已被「全量重生」换了新 session_id
            // → Agent 侧那个旧会话已无人认领，应中止，别再烧额度
            log.debug("heartbeat 未知 session_id={}，按无人认领答复", sessionId);
            return Map.of("tracked", false);
        }
        Task task = tasks.get(0);
        // 终态任务无需续期（迟到的 completed/failed 之后也会被终态检查丢掉），
        // 同时告知 Agent「已无人认领」——它跑完的回调同样会被丢弃
        if ("completed".equals(task.getStatus())
                || "failed".equals(task.getStatus())
                || "expired".equals(task.getStatus())) {
            log.debug("heartbeat 任务 {} 已终态 (status={})，按无人认领答复",
                    task.getId(), task.getStatus());
            return Map.of("tracked", false);
        }
        stuckTaskWatchdog.watch(task.getId(), task.getGenType());
        log.debug("heartbeat 任务 {} 看门狗续期 (status={}, genType={})",
                task.getId(), task.getStatus(), task.getGenType());
        return Map.of("tracked", true);
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
