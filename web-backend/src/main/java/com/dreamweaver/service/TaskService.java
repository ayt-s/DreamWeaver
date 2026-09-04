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

    /**
         * 删除历史作品。
         * 终态任务直接删除；非终态任务先通知 Agent 取消排队/停止生成，再删本地记录。
         */
        void deleteTask(Long id);

    /**
     * 重新生成：以原任务的 prompt + genType 提交一个全新任务（保留旧记录作历史）。
     * 仅允许终态任务发起。
     */
    TaskResponse regenerateTask(Long id);
}