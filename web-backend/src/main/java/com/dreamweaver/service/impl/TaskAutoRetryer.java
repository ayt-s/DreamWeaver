package com.dreamweaver.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.dreamweaver.entity.Task;
import com.dreamweaver.mapper.TaskMapper;
import com.dreamweaver.service.TaskService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.redisson.api.RBucket;
import org.redisson.api.RedissonClient;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * 失败任务自动重试器：画廊中失败/过期的任务无需手动逐个「重新生成」，
 * 定时扫描 → 按 agnes 平台限制节流 → 原地重跑（复用 regenerateTask，保持同一 id）。
 *
 * <p>agnes 限制适配（视频 RPM≈2、队列满 503 常见）：
 * - 视频任务：全局至少间隔 {@code video-spacing-seconds}（默认 90s）才重试一个，每轮最多 1 个
 * - 图片任务：间隔 30s、每轮最多 2 个
 * - 单任务最多重试 {@code max-attempts} 次（Redis 计数，TTL 24h），防止无限空转
 * - 仅重试「瞬时性失败」（超时/队列繁忙/限流/断连/失联/空消息）；参数类错误不重试
 * - 失败后 min-age-seconds 冷却，避免抖动期反复重试
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class TaskAutoRetryer {

    private static final Set<String> TARGET_STATUSES = Set.of("failed", "expired");
    private static final Set<String> VIDEO_GEN_TYPES = Set.of("text_video", "image_video");
    private static final String RETRY_COUNT_KEY = "task:retry:count:";
    private static final String VIDEO_LAST_KEY = "task:retry:video:last";
    private static final String IMAGE_LAST_KEY = "task:retry:image:last";
    private static final long RETRY_COUNT_TTL_S = 24 * 3600;

    /** 真正的硬性失败不自动重试（重试没有意义）：任务已删/权限/余额类 */
    private static final Pattern NON_RETRYABLE = Pattern.compile(
            "不存在|无权限|权限|余额|insufficient|denied|unauthorized");

    private final TaskMapper taskMapper;
    private final TaskService taskService;
    private final RedissonClient redisson;

    @Value("${app.retry.enabled:true}")
    private boolean enabled;
    @Value("${app.retry.min-age-seconds:180}")
    private long minAgeSeconds;
    @Value("${app.retry.max-attempts:3}")
    private int maxAttempts;
    @Value("${app.retry.video-spacing-seconds:90}")
    private long videoSpacingSeconds;
    @Value("${app.retry.image-spacing-seconds:30}")
    private long imageSpacingSeconds;
    @Value("${app.retry.max-video-per-sweep:1}")
    private int maxVideoPerSweep;
    @Value("${app.retry.max-image-per-sweep:2}")
    private int maxImagePerSweep;

    @Scheduled(fixedDelayString = "${app.retry.interval-ms:60000}", initialDelay = 60_000)
    public void sweep() {
        if (!enabled) {
            return;
        }
        LocalDateTime cutoff = LocalDateTime.now().minusSeconds(minAgeSeconds);
        List<Task> candidates = taskMapper.selectList(
                new LambdaQueryWrapper<Task>()
                        .in(Task::getStatus, TARGET_STATUSES)
                        .lt(Task::getUpdatedAt, cutoff)
                        .orderByAsc(Task::getId)
                        .last("LIMIT 30"));
        if (candidates.isEmpty()) {
            return;
        }

        int videoRetried = 0;
        int imageRetried = 0;
        for (Task t : candidates) {
            String msg = t.getErrorMessage();
            if (msg != null && NON_RETRYABLE.matcher(msg).find()) {
                log.debug("自动重试器跳过参数类失败 id={}: {}", t.getId(), msg);
                continue;
            }
            boolean isVideo = VIDEO_GEN_TYPES.contains(t.getGenType());
            if (isVideo && videoRetried >= maxVideoPerSweep) continue;
            if (!isVideo && imageRetried >= maxImagePerSweep) continue;

            // 单任务自动重试次数（Redis 计数，防无限空转）
            String countKey = RETRY_COUNT_KEY + t.getId();
            RBucket<String> ck = redisson.getBucket(countKey);
            int attempts = 0;
            String cntStr = ck.get();
            if (cntStr != null) {
                try {
                    attempts = Integer.parseInt(cntStr);
                } catch (NumberFormatException ignore) {
                }
            }
            if (attempts >= maxAttempts) {
                continue;
            }

            // 全局节流：视频更慢（对齐平台视频 RPM）
            String lastKey = isVideo ? VIDEO_LAST_KEY : IMAGE_LAST_KEY;
            long spacing = isVideo ? videoSpacingSeconds : imageSpacingSeconds;
            String last = redisson.<String>getBucket(lastKey).get();
            if (last != null) {
                try {
                    long elapsed = (System.currentTimeMillis() - Long.parseLong(last)) / 1000;
                    if (elapsed < spacing) {
                        continue;
                    }
                } catch (NumberFormatException ignore) {
                }
            }

            try {
                // 原地重跑：同一 id，复用原 prompt/genType；非终态/已删会被拒绝并跳过
                taskService.regenerateTask(t.getId());
                ck.set(String.valueOf(attempts + 1), Duration.ofSeconds(RETRY_COUNT_TTL_S));
                redisson.getBucket(lastKey).set(String.valueOf(System.currentTimeMillis()),
                        Duration.ofMinutes(60));
                if (isVideo) {
                    videoRetried++;
                } else {
                    imageRetried++;
                }
                log.info("自动重试: id={} genType={} 第{}次 ← {}", t.getId(), t.getGenType(),
                        attempts + 1, msg == null || msg.isBlank() ? "(无错误消息)" : truncate(msg));
            } catch (Exception e) {
                // 已被用户抢先重新生成/删除（非终态）→ 跳过
                log.info("自动重试 id={} 跳过: {}", t.getId(), e.getMessage());
            }
        }
    }

    private static String truncate(String s) {
        return s.length() <= 80 ? s : s.substring(0, 80);
    }
}