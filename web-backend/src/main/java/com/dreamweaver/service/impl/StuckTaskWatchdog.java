package com.dreamweaver.service.impl;

import com.dreamweaver.entity.Task;
import com.dreamweaver.mapper.TaskMapper;
import jakarta.annotation.PostConstruct;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.redisson.api.RMapCache;
import org.redisson.api.RedissonClient;
import org.redisson.api.map.event.EntryExpiredListener;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.Set;
import java.util.concurrent.TimeUnit;

/**
 * Redis TTL 看门狗（RMapCache 实现）：任务进入排期时写 watchKey（TTL 10 分钟），
 * 回调完成/失败或删除任务时移除；条目过期即说明 Agent 侧失联未回调 →
 * 转 failed，前端可用「重新生成」恢复。
 *
 * <p>为什么用 RMapCache 而不是 RBucket + keyspace 通知：本机 Redis 3.2.100
 * （微软归档 Windows 移植版）实测不发布 {@code __keyevent@*__:expired}
 * 事件，订阅端收不到消息；RMapCache 的过期事件由 Redisson 客户端侧
 * eviction 调度器 + 自带 pub/sub 通道驱动，不依赖服务器 keyspace 通知，
 * 旧版 Redis 同样可用。
 *
 * <p>根因背景：Agent 会话纯内存，Agent 服务重启后旧任务永久收不到回调，
 * Java 行卡 queued 无法自愈。看门狗保证任何未闭环任务 10 分钟内进入终态失败态。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class StuckTaskWatchdog {

    private static final String WATCHDOG_NAME = "dw:task:watchdog";
    /** 默认超时：单镜图片/短任务 10 分钟内未闭环视为僵死 */
    private static final long STALE_TTL_MINUTES = 10;
    /** 视频任务超时：文生视频/图生视频含图片生成 + 逐镜提交(全局节流) + 拼接，
     * 生成时常可超 10 分钟——按类型加长，避免误杀还在正常生成的任务 */
    private static final long VIDEO_STALE_TTL_MINUTES = 30;
    /** 判断是否视频类任务（需更长的闭环时间） */
    private static final java.util.Set<String> VIDEO_GEN_TYPES =
            java.util.Set.of("text_video", "image_video");
    private static final Set<String> NON_TERMINAL = Set.of("pending", "queued");
    private static final String STALE_MSG = "生成超时未回调（Agent 会话可能失联），可点击「重新生成」恢复";

    private final RedissonClient redisson;
    private final TaskMapper taskMapper;

    private RMapCache<String, String> cache;

    @PostConstruct
    public void init() {
        cache = redisson.getMapCache(WATCHDOG_NAME);
        // 过期事件由 Redisson 客户端侧 eviction 调度器驱动（不依赖服务器 keyspace 通知）
        cache.addListener((EntryExpiredListener<String, String>) event -> onKeyExpired(event.getKey()));
        log.info("StuckTaskWatchdog: RMapCache 过期监听已注册 (ttl={}min, cache={})", STALE_TTL_MINUTES, WATCHDOG_NAME);
    }

    /** 任务进入排期：武装看门狗条目，TTL 内未回调则转 failed。
     * 视频任务用更长 TTL（提交有全局节流 + 平台队列可能繁忙）。 */
    public void watch(Long taskId) {
        watch(taskId, null);
    }

    public void watch(Long taskId, String genType) {
        long ttl = VIDEO_GEN_TYPES.contains(genType) ? VIDEO_STALE_TTL_MINUTES : STALE_TTL_MINUTES;
        cache.put(String.valueOf(taskId), "queued", ttl, TimeUnit.MINUTES);
        log.debug("StuckTaskWatchdog: 武装 id={} ttl={}min genType={}", taskId, ttl, genType);
    }

    /** 任务已闭环（回调完成/失败）或已删除：解除看门狗。 */
    public void clear(Long taskId) {
        cache.remove(String.valueOf(taskId));
        log.debug("StuckTaskWatchdog: 解除 id={}", taskId);
    }

    private void onKeyExpired(String key) {
        if (key == null) {
            return;
        }
        long taskId;
        try {
            taskId = Long.parseLong(key);
        } catch (NumberFormatException e) {
            return;
        }
        Task task = taskMapper.selectById(taskId);
        if (task == null || !NON_TERMINAL.contains(task.getStatus())) {
            return;
        }
        Task patch = new Task();
        patch.setId(taskId);
        patch.setStatus("failed");
        patch.setErrorMessage(STALE_MSG);
        patch.setUpdatedAt(LocalDateTime.now());
        taskMapper.updateById(patch);
        log.warn("StuckTaskWatchdog: id={} 看门狗 TTL 过期 → failed（原 status={}）", taskId, task.getStatus());
    }
}