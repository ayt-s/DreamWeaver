package com.dreamweaver.controller;

import com.dreamweaver.common.CommonResult;
import com.dreamweaver.dto.CreateTaskRequest;
import com.dreamweaver.dto.TaskResponse;
import com.dreamweaver.service.TaskService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;

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
}