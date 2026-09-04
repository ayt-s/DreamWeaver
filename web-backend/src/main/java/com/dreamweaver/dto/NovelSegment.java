package com.dreamweaver.dto;

import lombok.Data;

import java.util.List;

/**
 * 单个分镜片段（小说转漫剧）。
 */
@Data
public class NovelSegment {

    /** 片段唯一 id（img0/vid0 命名可作展示锚点） */
    private String id;

    /** 所属章节序号（对应 agent-service 的 chapter 字段） */
    private Integer chapter;

    /** 分镜标题 */
    private String title;

    /** 情节描述（画面叙事） */
    private String plot;

    /** 出镜角色列表 */
    private List<String> characters;

    /** 场景描述 */
    private String scene;

    /** 镜头机位/运镜 */
    private String camera;

    /** 时长（秒） */
    private Integer seconds;

    /** 情绪/氛围标签 */
    private String mood;

    /** 图片生成 prompt */
    private String imagePrompt;

    /** 视频生成 prompt */
    private String videoPrompt;
}
