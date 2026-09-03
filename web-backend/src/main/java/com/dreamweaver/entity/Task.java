package com.dreamweaver.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.time.LocalDateTime;

/**
 * 创作任务实体（对应 creative_task 表）。
 * 注意：实体只做数据映射，不出现在 controller 返回里（一律走 dto）。
 */
@Data
@TableName("creative_task")
public class Task {

    @TableId(type = IdType.AUTO)
    private Long id;

    /** FastAPI 侧 session_id（LangGraph thread_id） */
    private String sessionId;

    private Long userId;

    /** pending/queued/script_writing/storyboard_writing/video_generating/... /completed/failed */
    private String status;

    /** 用户原始需求 */
    private String prompt;

    /** 模型侧产物（视频 URL 数组 JSON / 分镜 JSON），Phase 1 简化存文本 */
    private String resultJson;

    private String errorMessage;

    private LocalDateTime createdAt;

    private LocalDateTime updatedAt;
}