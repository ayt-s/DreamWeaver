package com.dreamweaver.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import com.baomidou.mybatisplus.annotation.Version;
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

    /** 生成类型：text_video(纯文本视频)/image_video(图生视频)/novel_image(小说转图) */
    private String genType;

    /** 用户原始需求 */
    private String prompt;

    /** 模型侧产物（视频 URL 数组 JSON / 分镜 JSON） */
    private String resultJson;

    /** 文生图产出的图片 URL 数组（JSON 格式） */
    private String imageUrls;

    /** Agnes 返回的异步任务 ID（用于幂等判断） */
    private String videoId;

    /** 当前分镜索引 */
    private Integer shotIndex;

    private String errorMessage;

    private LocalDateTime createdAt;

    private LocalDateTime updatedAt;

    /**
     * 乐观锁版本号：回调更新时用 expected_version 防止乱序覆盖。
     * MyBatis-Plus @Version 注解自动处理：
     * - 更新时自动加 1
     * - WHERE 条件带 version 匹配，不匹配则影响行数为 0
     */
    @Version
    private Integer version;
}