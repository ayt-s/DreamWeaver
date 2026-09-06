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
            @RequestParam(required = false) String genType) {
        return CommonResult.ok(taskService.listTasks(page, size, genType));
    }

    /** 删除历史作品（仅终态；非终态返回 400） */
    @DeleteMapping("/{id}")
    public CommonResult<Void> deleteTask(@PathVariable Long id) {
        taskService.deleteTask(id);
        return CommonResult.ok(null);
    }

    /** 重新生成：同一任务原地重跑（复用原 prompt + genType） */
    @PostMapping("/{id}/regenerate")
    public CommonResult<TaskResponse> regenerateTask(@PathVariable Long id) {
        return CommonResult.ok(taskService.regenerateTask(id));
    }

    /** 查询任务段配置 + 每段已有视频 URL（画布段列表 UI） */
    @GetMapping("/{id}/segments")
    public CommonResult<java.util.List<java.util.Map<String, Object>>> getSegments(
            @PathVariable Long id) {
        return CommonResult.ok(taskService.getSegments(id));
    }

    /** 穿帮段重新生成：勾选段重生（可改提示词）+ 其余段复用 + 重新拼接成片 */
    @PostMapping("/{id}/rework")
    public CommonResult<TaskResponse> reworkTask(
            @PathVariable Long id,
            @Valid @RequestBody com.dreamweaver.dto.ReworkTaskRequest request) {
        return CommonResult.ok(taskService.reworkTask(
                id, request.getReworkIndices(), request.getEditedPrompts()));
    }
}