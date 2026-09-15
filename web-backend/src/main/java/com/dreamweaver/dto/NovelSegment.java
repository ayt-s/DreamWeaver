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

    /** 镜头机位/运镜（自由文本，供人读与拼提示词） */
    private String camera;

    /** 结构化景别：远景/全景/中景/近景/特写（画布 dropdown 用；空=不指定） */
    private String shotSize;

    /** 结构化机位：平视/俯拍/仰拍/航拍/过肩（空=不指定） */
    private String angle;

    /** 结构化运镜：固定/推近/拉远/摇镜/移镜/跟拍/环绕（空=不指定） */
    private String movement;

    /** 时长（秒） */
    private Integer seconds;

    /** 情绪/氛围标签 */
    private String mood;

    /** 图片生成 prompt */
    private String imagePrompt;

    /** 视频生成 prompt */
    private String videoPrompt;
}
