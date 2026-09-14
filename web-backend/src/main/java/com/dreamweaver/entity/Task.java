package com.dreamweaver.entity;

import com.baomidou.mybatisplus.annotation.FieldFill;
import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
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

    /** 生成类型：text_video(纯文本视频)/image_video(图生视频)/text_image(文生图) */
    private String genType;

    /** 用户原始需求 */
    private String prompt;

    /** 模型侧产物（视频 URL 数组 JSON / 分镜 JSON） */
    private String resultJson;

    /** 文生图产出的图片 URL 数组（JSON 格式） */
    private String imageUrls;

    /** 提交时的段配置数组 JSON（重生输入源：{prompt,image_url,reference_images,seconds,aspect_ratio}） */
    private String segmentsJson;

    /**
     * 可灵式精细控制参数 JSON（风格/负面词/总时长/镜头数/元素绑定）。
     * 单独落库是为了 regenerate（全量重新生成）时不丢这些参数——
     * 该链路只从 entity 重建 CreateTaskRequest，不从请求体取。
     */
    private String genParamsJson;

    /** 重生覆盖前的旧 result_json（回滚用） */
    private String prevResultJson;

    /** 终态完成/失败时间；与 updated_at 区分——自动重试器刷新 updated_at 时不覆盖此字段 */
    private LocalDateTime completedAt;

    /** 草稿标记：0=成品，1=草稿（默认）。新生成物默认进草稿区，人工确认后转成品 */
    @TableField(fill = FieldFill.INSERT)
    private Integer isDraft;

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
     * @TableField(fill=INSERT) 确保 insert 时自动填 0（列 NOT NULL）
     */
    @Version
    @TableField(fill = FieldFill.INSERT)
    private Integer version;
}