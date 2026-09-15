package com.dreamweaver.controller;

import com.dreamweaver.common.CommonResult;
import com.dreamweaver.dto.CreateTaskRequest;
import com.dreamweaver.dto.TaskListResponse;
import com.dreamweaver.dto.TaskResponse;
import com.dreamweaver.service.TaskService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * 任务 controller。只做参数接收 + 响应组装，不写业务逻辑。
 */
@RestController
@RequestMapping("/api/tasks")
@RequiredArgsConstructor
public class TaskController {

    private final TaskService taskService;

    @PostMapping("/video")
    public CommonResult<TaskResponse> createVideoTask(@Valid @RequestBody CreateTaskRequest request) {
        return CommonResult.ok(taskService.createTask(request));
    }

    @GetMapping("/{id}")
    public CommonResult<TaskResponse> getTask(@PathVariable Long id) {
        return CommonResult.ok(taskService.getTask(id));
    }

    @GetMapping
    public CommonResult<TaskListResponse> listTasks(
            @RequestParam(defaultValue = "1") int page,
            @RequestParam(defaultValue = "10") int size,
            @RequestParam(required = false) String genType,
            @RequestParam(required = false) Boolean draft,
            // 画布「从历史作品选取」需要素材（source=canvas_asset），画廊不需要
            @RequestParam(defaultValue = "false") boolean includeAssets) {
        return CommonResult.ok(taskService.listTasks(page, size, genType, draft, includeAssets));
    }

    /** 删除历史作品（仅终态；非终态返回 400） */
    @DeleteMapping("/{id}")
    public CommonResult<Void> deleteTask(@PathVariable Long id) {
        taskService.deleteTask(id);
        return CommonResult.ok(null);
    }

    /**
     * 重新生成：同一任务原地重跑（复用原 prompt + genType）。
     * body 可选（body 为 null/空对象时行为与改造前完全一致）；
     * 传了精细控制参数则用其覆盖 entity 里保存的历史参数并写回 gen_params_json。
     * 不加 @Valid：body 里的 prompt 对重生无意义，且空体不应触发 NotBlank 校验。
     */
    @PostMapping("/{id}/regenerate")
    public CommonResult<TaskResponse> regenerateTask(
            @PathVariable Long id,
            @RequestBody(required = false) CreateTaskRequest body) {
        return CommonResult.ok(taskService.regenerateTask(id, body));
    }

    /** 查询任务段配置 + 每段已有视频 URL（画布段列表 UI） */
    @GetMapping("/{id}/segments")
    public CommonResult<java.util.List<java.util.Map<String, Object>>> getSegments(
            @PathVariable Long id) {
        return CommonResult.ok(taskService.getSegments(id));
    }

    /** 切换草稿标记：true 移入草稿区，false 移出（回成品区） */
    @PatchMapping("/{id}/draft")
    public CommonResult<TaskResponse> setDraft(
            @PathVariable Long id,
            @RequestParam(defaultValue = "true") boolean draft) {
        return CommonResult.ok(taskService.setDraft(id, draft));
    }

    /**
     * 拼接成片：把该任务的分段视频按顺序拼成一条长视频。
     * 标准模式（一句话生成）此前只有平铺的分段，没有任何拼接入口；
     * 纯本地 ffmpeg，不消耗生成额度，重复调用幂等。
     */
    @PostMapping("/{id}/concat")
    public CommonResult<TaskResponse> concatTask(@PathVariable Long id) {
        return CommonResult.ok(taskService.concatTask(id));
    }

    /** 穿帮段重新生成：勾选段重生（可改提示词）+ 其余段复用 + 重新拼接成片 */
    @PostMapping("/{id}/rework")
    public CommonResult<TaskResponse> reworkTask(
            @PathVariable Long id,
            @Valid @RequestBody com.dreamweaver.dto.ReworkTaskRequest request) {
        return CommonResult.ok(taskService.reworkTask(
                id, request.getReworkIndices(), request.getEditedPrompts()));
    }

    /** 批量穿帮段重新生成：一次提交多个任务的段重生，每个任务独立执行 */
    @PostMapping("/batch-rework")
    public CommonResult<com.dreamweaver.dto.BatchReworkResult> batchRework(
            @Valid @RequestBody com.dreamweaver.dto.BatchReworkRequest request) {
        return CommonResult.ok(taskService.batchRework(request));
    }
}