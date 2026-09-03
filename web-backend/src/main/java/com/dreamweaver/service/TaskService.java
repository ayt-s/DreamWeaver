package com.dreamweaver.service;

import com.dreamweaver.dto.CreateTaskRequest;
import com.dreamweaver.dto.TaskResponse;

/**
 * 创作任务服务接口。
 * 铁律：controller 只调 service；跨模块编排（落库 → 调 FastAPI → 回写）在 impl。
 */
public interface TaskService {

    /** 提交创作任务：落库 creative_task(pending) → 调 FastAPI /v1/tasks/video → 回写 session_id */
    TaskResponse createTask(CreateTaskRequest request);

    /** 查询任务状态 */
    TaskResponse getTask(Long id);

    /** 任务列表（倒序，供画廊页展示） */
    java.util.List<TaskResponse> listTasks(int limit);
}