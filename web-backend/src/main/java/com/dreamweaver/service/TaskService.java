package com.dreamweaver.service;

import com.dreamweaver.dto.CreateTaskRequest;
import com.dreamweaver.dto.TaskListResponse;
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

    /** 任务分页列表（倒序，含 genType 筛选，供画廊页展示） */
    TaskListResponse listTasks(int page, int size, String genType);

    /**
         * 删除历史作品。
         * 终态任务直接删除；非终态任务先通知 Agent 取消排队/停止生成，再删本地记录。
         */
        void deleteTask(Long id);

    /**
     * 重新生成：同一任务原地重跑（保留 id，复用原 prompt + genType）。
     * 仅允许终态任务发起。
     */
    TaskResponse regenerateTask(Long id);
}