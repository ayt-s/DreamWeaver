package com.dreamweaver.service.impl;

import com.dreamweaver.entity.Task;
import com.dreamweaver.mapper.TaskMapper;
import com.dreamweaver.service.TaskService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.redisson.api.RBucket;
import org.redisson.api.RedissonClient;
import org.springframework.test.util.ReflectionTestUtils;

import java.time.Duration;
import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * {@link TaskAutoRetryer} 的三道闸门 + 每轮配额。
 *
 * <p><b>为什么必须测这个类：</b>它会在无人值守时**自动花钱**（原地重跑 = 重新调
 * agnes）。三道闸门现在完全没有保护，任何一处失效都会变成无限重试烧额度：
 * <ol>
 *   <li>{@code NON_RETRYABLE} 正则：参数类/硬性失败不重试</li>
 *   <li>Redis 计数 {@code >= maxVideoAttempts / maxImageAttempts}：单任务次数上限</li>
 *   <li>全局节流 {@code elapsed < spacing}：对齐平台 RPM，避免 503</li>
 * </ol>
 * 另有每轮配额（视频 1 个 / 图片 2 个）防止一轮内爆发。
 *
 * <p>纯 Mockito，不启 Spring 上下文、不连 Redis/MySQL。
 */
@ExtendWith(MockitoExtension.class)
class TaskAutoRetryerTest {

    private static final String COUNT_PREFIX = "task:retry:count:";

    @Mock
    private TaskMapper taskMapper;
    @Mock
    private TaskService taskService;
    @Mock
    private RedissonClient redisson;
    @Mock
    private RBucket<String> countBucket;
    @Mock
    private RBucket<String> lastBucket;

    @InjectMocks
    private TaskAutoRetryer retryer;

    @BeforeEach
    void setUp() {
        // @Value 字段在单测里不会被注入（boolean 默认 false → sweep() 直接 return）
        ReflectionTestUtils.setField(retryer, "enabled", true);
        ReflectionTestUtils.setField(retryer, "minAgeSeconds", 180L);
        ReflectionTestUtils.setField(retryer, "maxVideoAttempts", 1);
        ReflectionTestUtils.setField(retryer, "maxImageAttempts", 3);
        ReflectionTestUtils.setField(retryer, "videoSpacingSeconds", 90L);
        ReflectionTestUtils.setField(retryer, "imageSpacingSeconds", 30L);
        ReflectionTestUtils.setField(retryer, "maxVideoPerSweep", 1);
        ReflectionTestUtils.setField(retryer, "maxImagePerSweep", 2);
    }

    /** 计数 key 走 countBucket，节流 key 走 lastBucket —— 与生产代码的两次 getBucket 对应 */
    private void stubBuckets() {
        doAnswer(inv -> {
            String key = inv.getArgument(0);
            return key.startsWith(COUNT_PREFIX) ? countBucket : lastBucket;
        }).when(redisson).getBucket(anyString());
    }

    private static Task task(long id, String genType, String errorMessage) {
        Task t = new Task();
        t.setId(id);
        t.setStatus("failed");
        t.setGenType(genType);
        t.setErrorMessage(errorMessage);
        return t;
    }

    private void candidates(Task... tasks) {
        when(taskMapper.selectList(any())).thenReturn(List.of(tasks));
    }

    // ------------------------------------------------------------ 闸门 1：正则

    @Test
    @DisplayName("闸门1：参数类/硬性失败不重试（正则命中直接跳过）")
    void nonRetryableErrorsAreSkipped() {
        candidates(
                task(1, "text_video", "该任务不存在"),
                task(2, "text_video", "余额不足 insufficient balance"),
                task(3, "text_video", "画布片段包含本地上传/内网图片，agnès 无法生成"),
                task(4, "text_video", "media must be a public URL"));

        retryer.sweep();

        verify(taskService, never()).regenerateTask(any());
        // 连 Redis 都不该碰：正则判定在计数之前
        verify(redisson, never()).getBucket(anyString());
    }

    @Test
    @DisplayName("闸门1：瞬时性失败（网络异常/超时/队列繁忙）要重试")
    void transientErrorsAreRetried() {
        candidates(task(1, "text_video", "视频提交所有 provider 都失败：[intl] 网络异常"));
        stubBuckets();

        retryer.sweep();

        verify(taskService, times(1)).regenerateTask(1L);
    }

    // -------------------------------------------------------- 闸门 2：次数上限

    @Test
    @DisplayName("闸门2：视频任务已达 max-video-attempts(1) → 不再重试")
    void videoRetryStopsAtMaxAttempts() {
        candidates(task(1, "text_video", "网络异常"));
        stubBuckets();
        when(countBucket.get()).thenReturn("1");   // 已重试 1 次 = 上限

        retryer.sweep();

        verify(taskService, never()).regenerateTask(any());
    }

    @Test
    @DisplayName("闸门2：图片任务上限更宽（3 次），第 2 次仍可重试")
    void imageRetryAllowsMoreAttempts() {
        candidates(task(1, "text_image", "网络异常"));
        stubBuckets();
        when(countBucket.get()).thenReturn("2");   // < 3

        retryer.sweep();

        verify(taskService, times(1)).regenerateTask(1L);
        // 计数写回 3，并带 TTL
        verify(countBucket).set("3", Duration.ofSeconds(3 * 3600));
    }

    @Test
    @DisplayName("闸门2：计数 key 存了非数字也不能炸（按 0 处理）")
    void malformedCounterIsTreatedAsZero() {
        candidates(task(1, "text_video", "网络异常"));
        stubBuckets();
        when(countBucket.get()).thenReturn("脏数据");

        retryer.sweep();

        verify(taskService, times(1)).regenerateTask(1L);
    }

    // ---------------------------------------------------------- 闸门 3：节流

    @Test
    @DisplayName("闸门3：距上次视频重试不足 90s → 跳过（对齐平台 RPM）")
    void videoSpacingBlocksTooFrequentRetry() {
        candidates(task(1, "text_video", "网络异常"));
        stubBuckets();
        when(lastBucket.get()).thenReturn(String.valueOf(System.currentTimeMillis() - 30_000)); // 30s 前

        retryer.sweep();

        verify(taskService, never()).regenerateTask(any());
    }

    @Test
    @DisplayName("闸门3：已超过间隔 → 放行，并写回时间戳")
    void videoSpacingAllowsAfterInterval() {
        candidates(task(1, "text_video", "网络异常"));
        stubBuckets();
        when(lastBucket.get()).thenReturn(String.valueOf(System.currentTimeMillis() - 120_000)); // 120s 前

        retryer.sweep();

        verify(taskService, times(1)).regenerateTask(1L);
        verify(lastBucket).set(anyString(), any(Duration.class));
    }

    @Test
    @DisplayName("闸门3：节流 key 值损坏时不能误判为「刚跑过」")
    void malformedSpacingValueDoesNotBlock() {
        candidates(task(1, "text_video", "网络异常"));
        stubBuckets();
        when(lastBucket.get()).thenReturn("不是数字");

        retryer.sweep();

        verify(taskService, times(1)).regenerateTask(1L);
    }

    // ---------------------------------------------------------- 每轮配额

    @Test
    @DisplayName("每轮配额：3 个候选视频任务，一轮最多重试 1 个")
    void atMostOneVideoPerSweep() {
        candidates(
                task(1, "text_video", "网络异常"),
                task(2, "text_video", "网络异常"),
                task(3, "video", "网络异常"));   // 非 VIDEO_GEN_TYPES → 按图片类处理
        stubBuckets();

        retryer.sweep();

        // 视频只放 1 个；第 3 个 genType="video" 不在 VIDEO_GEN_TYPES 里，被当图片类
        verify(taskService, times(1)).regenerateTask(1L);
        verify(taskService, times(1)).regenerateTask(3L);
        verify(taskService, never()).regenerateTask(2L);
    }

    @Test
    @DisplayName("每轮配额：图片任务一轮最多 2 个")
    void atMostTwoImagesPerSweep() {
        candidates(
                task(1, "text_image", "网络异常"),
                task(2, "text_image", "网络异常"),
                task(3, "text_image", "网络异常"));
        stubBuckets();

        retryer.sweep();

        verify(taskService, times(2)).regenerateTask(any());
    }

    // ------------------------------------------------------------ 其他

    @Test
    @DisplayName("regenerateTask 抛异常（被用户抢先重生/删除）→ 吞掉，不影响后续候选")
    void regenerateFailureDoesNotAbortSweep() {
        candidates(
                task(1, "text_image", "网络异常"),
                task(2, "text_image", "网络异常"));
        stubBuckets();
        // 注意：regenerateTask 返回 void，必须用 doThrow 而不是 when(...)
        doThrow(new IllegalArgumentException("任务未完成，无法重新生成"))
                .when(taskService).regenerateTask(1L);

        retryer.sweep();

        verify(taskService, times(1)).regenerateTask(1L);
        verify(taskService, times(1)).regenerateTask(2L);   // 第 2 个仍被处理
    }

    @Test
    @DisplayName("enabled=false → 完全不动（一键关闭自动花钱）")
    void disabledRetryerDoesNothing() {
        ReflectionTestUtils.setField(retryer, "enabled", false);

        retryer.sweep();

        // 连候选都不查（无打桩，避免 Mockito 严格模式把多余打桩判为失败）
        verify(taskMapper, never()).selectList(any());
        verify(taskService, never()).regenerateTask(any());
    }

    @Test
    @DisplayName("没有候选任务 → 直接返回，不碰 Redis")
    void emptyCandidatesShortCircuits() {
        when(taskMapper.selectList(any())).thenReturn(List.of());

        retryer.sweep();

        verify(redisson, never()).getBucket(anyString());
    }

    @Test
    @DisplayName("成功重试后计数 +1，且按任务 id 独立计数")
    void counterIsPerTask() {
        candidates(task(7, "text_video", "网络异常"));
        stubBuckets();
        when(countBucket.get()).thenReturn(null);   // 第一次，没有计数

        retryer.sweep();

        verify(redisson).getBucket(COUNT_PREFIX + 7L);
        verify(countBucket).set("1", Duration.ofSeconds(3 * 3600));
    }
}
