package com.dreamweaver.dto;

import lombok.Data;

/**
 * FastAPI 回调请求体。
 * Phase 2 新增，用于接收 /internal/notify 回调。
 */
@Data
public class NotifyRequest {

    /** Agnes 返回的异步任务 ID */
    private String video_id;

    /** MinIO 持久化 URL */
    private String video_url;

    /** 当前分镜索引 */
    private Integer shot_index;

    /** LangGraph 会话 ID */
    private String session_id;

    /** 完成状态：completed / failed */
    private String status;

    /** 失败原因（status=failed 时填写） */
    private String error_message;
}
