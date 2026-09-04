package com.dreamweaver.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.time.LocalDateTime;

/**
 * 小说转漫剧项目（对应 novel_project 表）。
 */
@Data
@TableName("novel_project")
public class NovelProject {

    @TableId(type = IdType.AUTO)
    private Long id;

    /** 项目名称 */
    private String projectName;

    /** 所属用户 */
    private Long userId;

    /** 原始小说文本 */
    private String novelText;

    /** 章节切分 JSON */
    private String chaptersJson;

    /** 角色/场景/风格等分析 JSON */
    private String analysisJson;

    /** 分镜片段 JSON 数组 */
    private String segmentsJson;

    /** 视觉风格 */
    private String visualStyle;

    /** 关联画布项目 id */
    private Long canvasProjectId;

    /** 状态：draft / ready / failed */
    private String status;

    /** 失败信息 */
    private String errorMessage;

    private LocalDateTime createdAt;

    private LocalDateTime updatedAt;
}
