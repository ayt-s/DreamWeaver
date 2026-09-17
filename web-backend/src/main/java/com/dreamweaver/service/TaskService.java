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

    /**
     * 任务分页列表（倒序，含 genType 分类 + draft 草稿筛选，供画廊页展示）。
     *
     * @param includeAssets 是否包含画布素材（source=canvas_asset）。画廊传 false——
     *                      一键文生图会在草稿区刷出 N 个素材任务，它们不是作品；
     *                      画布的「从历史作品选取」面板传 true。
     */
    TaskListResponse listTasks(int page, int size, String genType, Boolean draft, boolean includeAssets,
            String status, String source);

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

    /**
     * 重新生成（带参数覆盖）：画廊「编辑参数」入口调用。
     * override 的非空字段覆盖 entity 里保存的历史精细控制参数，并写回 gen_params_json；
     * override 为 null 时等价于单参重载（自动重试器路径）。
     * 仅允许终态任务发起。
     */
    TaskResponse regenerateTask(Long id, CreateTaskRequest override);

    /**
     /** 穿帮段重新生成：勾选段重新生成（可改提示词）、未勾选段复用已有视频，
      * 全部段由 agent 重新拼接成片。仅允许 completed 且有段配置的任务。
      */
     TaskResponse reworkTask(Long id, java.util.List<Integer> reworkIndices,
             java.util.Map<String, String> editedPrompts);

     /** 批量穿帮段重新生成：一次提交多个任务的段重生，每个任务独立执行。 */
     com.dreamweaver.dto.BatchReworkResult batchRework(
             com.dreamweaver.dto.BatchReworkRequest request);

    /** 查询任务的段配置 + 每段已有视频 URL（供画布段列表 UI 展示） */
    java.util.List<java.util.Map<String, Object>> getSegments(Long id);

    /** 切换草稿标记：isDraft=true 移入草稿区，false 移出（成品区） */
    TaskResponse setDraft(Long id, boolean isDraft);

    /**
     * 拼接成片：把该任务已生成的 N 段视频拼成一条长视频（标准模式此前没有用户入口）。
     * 纯本地 ffmpeg，不消耗生成额度；幂等（已有成片直接返回）。
     */
    TaskResponse concatTask(Long id);
}