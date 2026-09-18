package com.dreamweaver.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
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
import java.util.List;
import java.util.Set;
import java.util.concurrent.TimeUnit;

/**
 * Redis TTL 看门狗（RMapCache 实现）：任务进入排期时写 watchKey（TTL 10 分钟），
 * 回调完成/失败或删除任务时移除；条目过期即说明 Agent 侧失联未回调 →
 * 转 interrupted（非终态），Agent 恢复后补发的迟到回调仍可落定，
 * 用户也可用「重新生成」恢复。
 *
 * <p>为什么用 RMapCache 而不是 RBucket + keyspace 通知：本机 Redis 3.2.100
 * （微软归档 Windows 移植版）实测不发布 {@code __keyevent@*__:expired}
 * 事件，订阅端收不到消息；RMapCache 的过期事件由 Redisson 客户端侧
 * eviction 调度器 + 自带 pub/sub 通道驱动，不依赖服务器 keyspace 通知，
 * 旧版 Redis 同样可用。
 *
 * <p>根因背景：Agent 会话纯内存，Agent 服务重启后旧任务永久收不到回调，
 * Java 行卡 queued 无法自愈。看门狗保证任何未闭环任务在 TTL 内（图片 10 分钟 / 视频 30 分钟）
 * 进入非终态 {@code interrupted}，再由 TaskAutoRetryer 接管重跑或用户手动重生。
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
    /** 超时文案：不再断言失败——Agent 会话可能正在重启恢复，迟到回调仍会被接受 */
    private static final String STALE_MSG = "生成超时未回调（Agent 可能正在恢复），可稍候或重新生成";

    private final RedissonClient redisson;
    private final TaskMapper taskMapper;

    private RMapCache<String, String> cache;

    @PostConstruct
    public void init() {
        cache = redisson.getMapCache(WATCHDOG_NAME);
        // 过期事件由 Redisson 客户端侧 eviction 调度器驱动（不依赖服务器 keyspace 通知）
        cache.addListener((EntryExpiredListener<String, String>) event -> onKeyExpired(event.getKey()));
        log.info("StuckTaskWatchdog: RMapCache 过期监听已注册 (ttl={}min, cache={})", STALE_TTL_MINUTES, WATCHDOG_NAME);
        rearmOpenTasks();
    }

    /**
     * 启动时按库里的真实状态**补武装**未闭环任务（2026-09-18 加）。
     *
     * <p>★ 为什么必须有：看门狗条目活在 Redis 的 RMapCache 里，**Redis 一重启 TTL 条目全丢**，
     * 而 arm 只在「任务进排期」那一刻发生 —— 丢了就再没人补，于是重启前创建、还没闭环的任务
     * 永远等不到过期事件，永久停在 queued/pending。
     * 实测踩到过这个死锁：任务卡 queued 30+ 分钟，`regenerate` 因 queued 被 400 挡、
     * Agent cancel 409、自动重试器又只认 failed/expired —— 三个出口全封死。
     * 启动补一遍，这条闭环才成立（配合 TaskAutoRetryer 认 interrupted）。
     */
    private void rearmOpenTasks() {
        try {
            List<Task> open = taskMapper.selectList(
                    new LambdaQueryWrapper<Task>()
                            .in(Task::getStatus, NON_TERMINAL)
                            .last("LIMIT 500"));
            for (Task t : open) {
                cache.put(String.valueOf(t.getId()), "queued", ttlMinutes(t.getGenType()), TimeUnit.MINUTES);
            }
            if (!open.isEmpty()) {
                log.info("StuckTaskWatchdog: 启动重新武装 {} 个未闭环任务（Redis 重启会丢 TTL 条目，不补就永久无人兜底）",
                        open.size());
            }
        } catch (Exception e) {
            // 补武装失败不能让应用起不来；新任务的武装不受影响
            log.warn("StuckTaskWatchdog: 启动重新武装失败（不影响新任务）: {}", e.toString());
        }
    }

    /** 按任务类型取 TTL：视频任务更长（含出图 + 逐镜提交节流 + 平台排队 + 拼接）。 */
    private long ttlMinutes(String genType) {
        return VIDEO_GEN_TYPES.contains(genType) ? VIDEO_STALE_TTL_MINUTES : STALE_TTL_MINUTES;
    }

    /** 任务进入排期：武装看门狗条目，TTL 内未回调则转 failed。
     * 视频任务用更长 TTL（提交有全局节流 + 平台队列可能繁忙）。 */
    public void watch(Long taskId) {
        watch(taskId, null);
    }

    public void watch(Long taskId, String genType) {
        long ttl = ttlMinutes(genType);
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
        // 不标 failed（终态不可逆，会作废 Agent 恢复后仍会补发的产物）：
        // 标 interrupted——非终态，迟到回调仍可落定 completed/failed
        patch.setStatus("interrupted");
        patch.setErrorMessage(STALE_MSG);
        patch.setUpdatedAt(LocalDateTime.now());
        taskMapper.updateById(patch);
        log.warn("StuckTaskWatchdog: id={} 看门狗 TTL 过期 → interrupted（原 status={}，等待 Agent 恢复或用户重新生成）",
                taskId, task.getStatus());
    }
}