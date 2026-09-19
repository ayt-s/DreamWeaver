package com.dreamweaver.service.impl;

import com.dreamweaver.dto.TaskResponse;
import com.dreamweaver.entity.Task;
import com.dreamweaver.mapper.TaskMapper;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.when;

/**
 * #20 的**读取侧**归一化：存量 {@code segments_json="[]"} 任务不能再骗过前端。
 *
 * <h3>为什么写侧改了还不够</h3>
 *
 * 写侧（{@link NotifyServiceImpl} 回调落库）改完只保证**新任务**不再写占位符；
 * 库里已有 27 条 {@code text_image} 任务的 {@code segments_json} 就是字面量 {@code "[]"}
 * （实测：{@code SELECT COUNT(*) ... WHERE TRIM(segments_json) IN ('[]')} = 27，
 * 全部 completed + text_image）。而前端多处判据是 {@code !!task.segmentsJson}（存在性）
 * —— 占位符是**真值**，于是「按段重生」入口照样渲染、点开 0 段可勾、提交撞 400。
 *
 * <p>所以两个读取出口都要归一化：
 * <ul>
 *   <li>{@code GET /api/tasks/{id}} → {@code segmentsJson}（前端据此决定渲染什么）</li>
 *   <li>{@code GET /api/tasks/{id}/segments} → 空列表</li>
 * </ul>
 * 这样不改前端也能立刻止血（走「旧任务无分镜」的置灰分支）。
 */
@ExtendWith(MockitoExtension.class)
class TaskSegmentsPlaceholderTest {

    private static final long TASK_ID = 21L;

    @Mock
    private TaskMapper taskMapper;

    private TaskServiceImpl service() {
        return new TaskServiceImpl(null, taskMapper, null, null, null,
                new TaskJsonCodec(new ObjectMapper()), null);
    }

    private Task task(String segmentsJson) {
        Task t = new Task();
        t.setId(TASK_ID);
        t.setStatus("completed");
        t.setGenType("text_image");
        t.setSegmentsJson(segmentsJson);
        return t;
    }

    @Test
    @DisplayName("★ #20：出参归一化 —— 占位符 \"[]\" 必须回 null（前端 !!segmentsJson 才不会误判）")
    void responseNormalizesPlaceholderToNull() {
        when(taskMapper.selectById(TASK_ID)).thenReturn(task("[]"));

        TaskResponse resp = service().getTask(TASK_ID);

        assertNull(resp.getSegmentsJson(),
                "占位符是真值，直接回传会让前端渲染出「0 段可勾」的段重生面板");
    }

    @Test
    @DisplayName("★ #20：getSegments 对占位符任务返回空列表（面板拿不到可勾的段）")
    void segmentsEndpointReturnsEmptyForPlaceholder() {
        when(taskMapper.selectById(TASK_ID)).thenReturn(task("[]"));

        assertTrue(service().getSegments(TASK_ID).isEmpty());
    }

    @Test
    @DisplayName("★ #20 回归护栏：真段配置的返回值一字不改（含出参与 getSegments）")
    void realSegmentsPassThroughUntouched() {
        String real = "[{\"prompt\":\"镜头一\",\"seconds\":4}]";
        when(taskMapper.selectById(TASK_ID)).thenReturn(task(real));

        assertEquals(real, service().getTask(TASK_ID).getSegmentsJson());
        List<java.util.Map<String, Object>> segs = service().getSegments(TASK_ID);
        assertEquals(1, segs.size());
        assertEquals("镜头一", segs.get(0).get("prompt"));
        assertNotNull(segs.get(0).get("index"));
    }

    @Test
    @DisplayName("★ #20：null / 空白 段配置同样回 null 与空列表（老行为不劣化）")
    void absentSegmentsStayAbsent() {
        when(taskMapper.selectById(TASK_ID)).thenReturn(task(null));
        assertNull(service().getTask(TASK_ID).getSegmentsJson());
        assertTrue(service().getSegments(TASK_ID).isEmpty());

        when(taskMapper.selectById(TASK_ID)).thenReturn(task("   "));
        assertNull(service().getTask(TASK_ID).getSegmentsJson());
        assertTrue(service().getSegments(TASK_ID).isEmpty());
    }
}
