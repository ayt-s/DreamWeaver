package com.dreamweaver.service.impl;

import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.baomidou.mybatisplus.core.metadata.TableInfoHelper;
import com.dreamweaver.dto.NotifyRequest;
import com.dreamweaver.entity.Task;
import com.dreamweaver.mapper.ApiQuotaMapper;
import com.dreamweaver.mapper.TaskMapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.ibatis.builder.MapperBuilderAssistant;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.concurrent.atomic.AtomicReference;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * {@link NotifyServiceImpl#handleCompletion} 三个分支的**写库口径**。
 *
 * <h3>为什么值得写这些测试</h3>
 *
 * 这个类的四个状态分支里，**只有 failed 分支用 `updateById`**，其余三个都是显式
 * `LambdaUpdateWrapper.set(...)`。这个不对称一开始只是"看起来可疑"，追下去发现它有
 * 真实的用户可见后果：
 *
 * <p>MyBatis-Plus 默认 `FieldStrategy.NOT_NULL` 会**跳过 null 字段**。于是当 agent 报
 * `failed` 但没带 `error_message` 时，库里上一轮留下的旧原因（例如回退排队时写的
 * 「Agent 重启恢复」）会**原样留着** —— 任务卡片显示的是一个与本次失败无关的假原因。
 * 排查时会被它带偏，而且看日志也看不出来（SQL 里那列压根没出现）。
 *
 * <p>这类"静默与兄弟分支不一致"的缺陷，靠读代码很难定论，靠单测一句话就能钉死。
 */
class NotifyServiceImplTest {

    private static final long TASK_ID = 36L;
    private static final String SESSION_ID = "s-1";

    private TaskMapper taskMapper;
    private StuckTaskWatchdog watchdog;
    private NotifyServiceImpl service;

    @BeforeAll
    static void initTableInfo() {
        // LambdaUpdateWrapper 要能把 `Task::getErrorMessage` 解析成列名，依赖实体的
        // TableInfo。纯单测（没有 Spring 上下文）下必须手动初始化，否则报
        // `can not find lambda cache for this entity`。
        TableInfoHelper.initTableInfo(
                new MapperBuilderAssistant(new MybatisConfiguration(), ""), Task.class);
    }

    @BeforeEach
    void setUp() {
        taskMapper = mock(TaskMapper.class);
        watchdog = mock(StuckTaskWatchdog.class);
        service = new NotifyServiceImpl(
                taskMapper,
                mock(ApiQuotaMapper.class),
                new ObjectMapper(),
                watchdog,
                mock(ImageCacheService.class),
                new TaskJsonCodec(new ObjectMapper()));
    }

    // ------------------------------------------------------------------ 用例

    @Test
    @DisplayName("★ failed 且 agent 没带原因 → 必须显式把旧的 error_message 清成 null")
    void failedWithoutMessageClearsStaleError() {
        // 场景：任务先被标记中断（带着一句提示），随后 agent 恢复并报 failed，但没带原因
        Task task = task("interrupted", "Agent 长时间未回调，已标记中断");
        when(taskMapper.selectList(any())).thenReturn(List.of(task));
        var wrapper = captureUpdate();

        service.handleCompletion(request("failed", null));

        assertEquals("failed", paramValueAfter(wrapper, "status"));
        assertNull(paramValueAfter(wrapper, "error_message"),
                "旧原因必须被显式清掉，否则卡片上会显示与本次失败无关的假原因");
        assertNotNull(paramValueAfter(wrapper, "completed_at"), "失败也是终态，要打完成时间");
    }

    @Test
    @DisplayName("failed 带了原因 → 原样落库")
    void failedWithMessageStoresIt() {
        when(taskMapper.selectList(any())).thenReturn(List.of(task("queued", null)));
        var wrapper = captureUpdate();

        service.handleCompletion(request("failed", "视频提交所有 provider 都失败：读超时"));

        assertEquals("视频提交所有 provider 都失败：读超时",
                paramValueAfter(wrapper, "error_message"));
    }

    @Test
    @DisplayName("failed 仍要写回本轮已聚合的产物与段配置（段重生要用）")
    void failedStillWritesAggregatedPayloads() {
        when(taskMapper.selectList(any())).thenReturn(List.of(task("queued", null)));
        var wrapper = captureUpdate();

        NotifyRequest req = request("failed", "炸了");
        req.setVideo_urls(List.of("http://mock/a.mp4"));
        req.setStoryboard("[{\"prompt\":\"镜头一\"}]");

        service.handleCompletion(req);

        assertEquals("[\"http://mock/a.mp4\"]", paramValueAfter(wrapper, "result_json"));
        assertEquals("[{\"prompt\":\"镜头一\"}]", paramValueAfter(wrapper, "segments_json"));
    }

    @Test
    @DisplayName("★ 回归护栏：failed 分支不得回退到 updateById")
    void failedBranchNeverUsesUpdateById() {
        when(taskMapper.selectList(any())).thenReturn(List.of(task("queued", null)));
        when(taskMapper.update(isNull(), any())).thenReturn(1);

        service.handleCompletion(request("failed", "炸了"));

        // updateById 的 NOT_NULL 策略无法表达"显式置 null"，用它就退回了老缺陷
        verify(taskMapper, never()).updateById(any(Task.class));
    }

    @Test
    @DisplayName("乐观锁冲突（update 返回 0）→ 不继续做副作用")
    void optimisticLockConflictStopsSideEffects() {
        when(taskMapper.selectList(any())).thenReturn(List.of(task("queued", null)));
        when(taskMapper.update(isNull(), any())).thenReturn(0);

        service.handleCompletion(request("failed", "炸了"));

        verify(taskMapper, never()).updateById(any(Task.class));
    }

    // ------------------------------------------- #6/#19：queued→queued 恢复报到

    @Test
    @DisplayName("★ #6/#19：任务本就 queued 时收到「回退报到」→ 重新武装看门狗，不写库")
    void queuedToQueuedRearmsWatchdog() {
        Task t = task("queued", null);
        t.setGenType("text_video");
        when(taskMapper.selectList(any())).thenReturn(List.of(t));

        service.handleCompletion(request("queued", "Agent 重启恢复，重新排队"));

        // 信号不再被当「非法状态跳转」丢掉 —— 这是本条缺陷的核心
        verify(watchdog).watch(TASK_ID, "text_video");
        // 状态没变 ⇒ 不该写库：不动 started_at/completed_at、不推 version、不碰产物
        verify(taskMapper, never()).update(isNull(), any());
        verify(taskMapper, never()).updateById(any(Task.class));
    }

    @Test
    @DisplayName("★ #6/#19：终态任务的「回退报到」不得把看门狗重新点着（防僵尸续期）")
    void queuedReportOnTerminalTaskIsIgnored() {
        when(taskMapper.selectList(any())).thenReturn(List.of(task("completed", null)));

        service.handleCompletion(request("queued", "Agent 重启恢复"));

        verify(watchdog, never()).watch(any(), any());
        verify(taskMapper, never()).update(isNull(), any());
    }

    @Test
    @DisplayName("★ #6/#19 回归护栏：非法跳变仍被挡（queued → video_generating 不在表里）")
    void illegalTransitionStillRejected() {
        when(taskMapper.selectList(any())).thenReturn(List.of(task("queued", null)));

        service.handleCompletion(request("video_generating", null));

        verify(taskMapper, never()).update(isNull(), any());
        verify(watchdog, never()).watch(any(), any());
    }

    @Test
    @DisplayName("★ #6/#19 回归护栏：interrupted → queued 仍按「回退排队」正常写库并重新武装")
    void interruptedToQueuedStillWritesAndRearms() {
        Task t = task("interrupted", "Agent 长时间未回调，已标记中断");
        t.setGenType("text_image");
        when(taskMapper.selectList(any())).thenReturn(List.of(t));
        var wrapper = captureUpdate();

        service.handleCompletion(request("queued", "Agent 重启恢复"));

        assertEquals("queued", paramValueAfter(wrapper, "status"));
        assertNull(paramValueAfter(wrapper, "completed_at"));
        verify(watchdog).watch(TASK_ID, "text_image");
    }

    // ------------------------------------------------- #20：storyboard 占位符

    @Test
    @DisplayName("★ #20：storyboard=\"[]\"（无分镜占位符）不得写进 segments_json")
    void emptyStoryboardPlaceholderIsNotStored() {
        when(taskMapper.selectList(any())).thenReturn(List.of(task("queued", null)));
        var wrapper = captureUpdate();

        NotifyRequest req = request("completed", null);
        req.setStoryboard("[]");

        service.handleCompletion(req);

        assertNull(paramValueAfter(wrapper, "segments_json"),
                "占位符 \"[]\" 会被前端 !!segmentsJson 当成有分镜 → 面板 0 段可勾 → 提交 400");
    }

    @Test
    @DisplayName("★ #20：字段缺省 / null / 坏 JSON 同样不写 segments_json")
    void missingOrBrokenStoryboardIsNotStored() {
        when(taskMapper.selectList(any())).thenReturn(List.of(task("queued", null)));
        var wrapper = captureUpdate();
        NotifyRequest req = request("completed", null);
        req.setStoryboard("  ");
        service.handleCompletion(req);
        assertNull(paramValueAfter(wrapper, "segments_json"));

        when(taskMapper.selectList(any())).thenReturn(List.of(task("queued", null)));
        wrapper = captureUpdate();
        NotifyRequest broken = request("completed", null);
        broken.setStoryboard("[[[");
        service.handleCompletion(broken);
        assertNull(paramValueAfter(wrapper, "segments_json"), "坏 JSON 按「无分段」处理");
    }

    @Test
    @DisplayName("#20：真分镜照旧落库（回归护栏，别把正常路径一起挡掉）")
    void realStoryboardIsStillStored() {
        when(taskMapper.selectList(any())).thenReturn(List.of(task("queued", null)));
        var wrapper = captureUpdate();

        NotifyRequest req = request("completed", null);
        req.setStoryboard("[{\"prompt\":\"镜头一\"}]");

        service.handleCompletion(req);

        assertEquals("[{\"prompt\":\"镜头一\"}]", paramValueAfter(wrapper, "segments_json"));
    }

    // ------------------------------------------------------------------ 工具

    private Task task(String status, String errorMessage) {
        Task t = new Task();
        t.setId(TASK_ID);
        t.setSessionId(SESSION_ID);
        t.setStatus(status);
        t.setErrorMessage(errorMessage);
        t.setUserId(1L);
        t.setVersion(0);
        return t;
    }

    private NotifyRequest request(String status, String errorMessage) {
        NotifyRequest r = new NotifyRequest();
        r.setSession_id(SESSION_ID);
        r.setStatus(status);
        r.setError_message(errorMessage);
        return r;
    }

    /** 接住 `taskMapper.update(null, wrapper)` 的第二个参数（service 调用后再取）。 */
    private AtomicReference<LambdaUpdateWrapper<Task>> captureUpdate() {
        AtomicReference<LambdaUpdateWrapper<Task>> captured = new AtomicReference<>();
        when(taskMapper.update(isNull(), any())).thenAnswer(inv -> {
            captured.set(inv.getArgument(1));
            return 1;
        });
        return captured;
    }

    /**
     * 从 SET 子句里取出某列的绑定值。
     *
     * `getSqlSet()` 形如 `status=#{ew.paramNameValuePairs.MPGENVAL1},error_message=#{...MPGENVAL5}`
     * —— 先拿到占位符名，再去参数表里查值，这样**能证明"这一列确实被写了、写的是什么"**，
     * 而不是只看到"SQL 里有这个词"。
     */
    private Object paramValueAfter(AtomicReference<LambdaUpdateWrapper<Task>> ref, String column) {
        LambdaUpdateWrapper<Task> wrapper = ref.get();
        assertNotNull(wrapper, "没有捕获到 LambdaUpdateWrapper");
        Matcher m = Pattern.compile(
                        column + "=#\\{ew\\.paramNameValuePairs\\.([A-Za-z0-9_]+)\\}")
                .matcher(wrapper.getSqlSet());
        assertTrue(m.find(), "SET 子句里没有 " + column + "：" + wrapper.getSqlSet());
        return wrapper.getParamNameValuePairs().get(m.group(1));
    }
}
