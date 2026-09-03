package com.dreamweaver.dto;

import lombok.Data;

import java.util.List;

/**
 * FastAPI 回调请求体。
 * Phase 2 新增，用于接收 /internal/notify 回调。
 * 2026-09 契约修复：关联键为 session_id（Java 侧无 video_id 列），
 * 主载荷 video_urls 为全量 URL 数组。
 */
@Data
public class NotifyRequest {

    /** Agnes 返回的异步任务 ID（审计用，Java 不按此查任务） */
    private String video_id;

    /** 单值兼容字段（已弃用，保留兼容旧回调） */
    private String video_url;

    /** 全量视频 URL 数组（主载荷） */
    private List<String> video_urls;

    /** 当前分镜索引（整会话回调为 null） */
    private Integer shot_index;

    /** LangGraph 会话 ID（Java 侧关联主键） */
    private String session_id;

    /** 完成状态：completed / failed */
    private String status;

    /** 失败原因（status=failed 时填写） */
    private String error_message;
}
