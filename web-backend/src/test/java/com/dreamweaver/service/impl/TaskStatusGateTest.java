package com.dreamweaver.service.impl;

import com.dreamweaver.entity.Task;
import com.dreamweaver.mapper.TaskMapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 「生成是否已停下」这道闸门在 {@code interrupted} 上的口径（★ 2026-09-19 修 #15 Java 一半）。
 *
 * <h3>为什么值得写</h3>
 *
 * 前端 {@code TaskCard.tsx:61} 把 {@code interrupted} 当**终态**，因此给它渲染
 * 「确认成品 / 退回草稿」「拼接成片」这些按钮；而 Java 侧的终态集合
 * （{@code TaskServiceImpl.TERMINAL_STATUSES}）**不含** interrupted ⇒ 按钮点下去必然 400
 * （「仅已终态的任务可…（当前=interrupted）」）。用户看到的是「按钮在那里、点了报错」。
 *
 * <p>两边的语义应该统一到：<b>interrupted 不是终态（看门狗兜底出的、会被
 * {@link TaskAutoRetryer} 自动重跑），但生成确实已经停下</b> —— 所以「整理类」操作应当放行，
 * 而「会话可能还活着」的那类判断（删除/重生前要不要先 cancel Agent 会话）仍按终态口径走。
 *
 * <p>纯 Mockito，不启 Spring、不连 MySQL。
 */
@ExtendWith(MockitoExtension.class)
class TaskStatusGateTest {

    private static final long TASK_ID = 36L;

    @Mock
    private TaskMapper taskMapper;

    /** 只注入 mapper，其余依赖按 @RequiredArgsConstructor 的字段顺序补 null */
    private TaskServiceImpl service() {
        return new TaskServiceImpl(null, taskMapper, null, null, null,
                new TaskJsonCodec(new ObjectMapper()), null);
    }

    private Task task(String status) {
        Task t = new Task();
        t.setId(TASK_ID);
        t.setStatus(status);
        t.setGenType("text_video");
        t.setVersion(0);
        return t;
    }

    // ------------------------------------------------------------ 拼接成片

    @Test
    @DisplayName("★ #15：interrupted 任务点「拼接成片」不再被终态闸门 400 挡下")
    void concatAllowsInterrupted() {
        when(taskMapper.selectById(TASK_ID)).thenReturn(task("interrupted"));

        // 闸门放行后才会走到「分段不足」这一步 —— 报错文案变了，就证明闸门确实放行了
        IllegalArgumentException e = assertThrows(IllegalArgumentException.class,
                () -> service().concatTask(TASK_ID, false));
        assertTrue(e.getMessage().contains("分段不足"),
                "应越过终态闸门、在分段校验处失败，实际: " + e.getMessage());
    }

    @Test
    @DisplayName("★ #15 回归护栏：真·进行中（queued/pending/video_generating）仍必须被挡")
    void concatStillRejectsRunning() {
        for (String running : new String[] {"queued", "pending", "video_generating"}) {
            when(taskMapper.selectById(TASK_ID)).thenReturn(task(running));
            IllegalArgumentException e = assertThrows(IllegalArgumentException.class,
                    () -> service().concatTask(TASK_ID, false),
                    running + " 是在飞任务，拼接会被后续产物覆盖，必须挡");
            assertTrue(e.getMessage().contains("正在生成中"),
                    running + " 应被挡下，实际: " + e.getMessage());
        }
    }

    @Test
    @DisplayName("completed 照旧放行（老行为不劣化）")
    void concatStillAllowsCompleted() {
        when(taskMapper.selectById(TASK_ID)).thenReturn(task("completed"));

        IllegalArgumentException e = assertThrows(IllegalArgumentException.class,
                () -> service().concatTask(TASK_ID, false));
        assertTrue(e.getMessage().contains("分段不足"), e.getMessage());
    }

    // -------------------------------------------------------------- 草稿

    @Test
    @DisplayName("★ #15：interrupted 任务可退回草稿（前端把它当终态渲染了这个按钮）")
    void setDraftAllowsInterrupted() {
        when(taskMapper.selectById(TASK_ID)).thenReturn(task("interrupted"));

        service().setDraft(TASK_ID, true);

        verify(taskMapper, times(1)).updateById(any(Task.class));
    }

    @Test
    @DisplayName("★ #15 回归护栏：queued 任务仍不能进草稿区")
    void setDraftStillRejectsQueued() {
        when(taskMapper.selectById(TASK_ID)).thenReturn(task("queued"));

        IllegalArgumentException e = assertThrows(IllegalArgumentException.class,
                () -> service().setDraft(TASK_ID, true));
        assertTrue(e.getMessage().contains("正在生成中"), e.getMessage());
        verify(taskMapper, never()).updateById(any(Task.class));
    }

    // ------------------------------------------------------- 「重生」入口

    @Test
    @DisplayName("★ #15：interrupted 仍可「重新生成」（它本来就是看门狗兜底出的非终态）")
    void regenerateAllowsInterrupted() {
        when(taskMapper.selectById(TASK_ID)).thenReturn(task("interrupted"));

        // 走到派发环节才失败（agentServiceProperties 为 null）——
        // 关键是没有在状态闸门处被拒
        Exception e = assertThrows(Exception.class,
                () -> service().regenerateTask(TASK_ID, null));
        assertFalse(e.getMessage() != null && e.getMessage().contains("无法重新生成"),
                "不该在状态闸门处被拒，实际: " + e.getMessage());
    }

    @Test
    @DisplayName("★ #15 回归护栏：queued 任务不能「重新生成」（避免与在飞生成双跑）")
    void regenerateRejectsQueued() {
        when(taskMapper.selectById(TASK_ID)).thenReturn(task("queued"));

        IllegalArgumentException e = assertThrows(IllegalArgumentException.class,
                () -> service().regenerateTask(TASK_ID, null));
        assertTrue(e.getMessage().contains("无法重新生成"), e.getMessage());
    }
}
