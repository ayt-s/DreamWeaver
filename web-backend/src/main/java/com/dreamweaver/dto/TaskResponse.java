package com.dreamweaver.dto;

import lombok.Data;

/**
 * 任务响应 dto。对外返回，禁止直接返回 entity。
 */
@Data
public class TaskResponse {

    private Long id;

    private String sessionId;

    private String status;

    /** 生成类型：text_video/image_video/text_image */
    private String genType;

    /** 生成产物 JSON（视频 URL 数组），前端轮询完成后展示视频用 */
    private String resultJson;

    /** 文生图产出的图片 URL 数组（JSON 格式） */
    private String imageUrls;

    /** 提交时的段配置数组 JSON；非空说明是画布多段任务，前端可开启「穿帮段重生」 */
    private String segmentsJson;

    /**
     * 保存的精细控制参数 JSON（风格提示词/负面提示词/总时长/镜头数/运镜倾向/元素绑定）。
     * 画廊「编辑参数」入口用它反序列化预填；null = 从未配置过。
     */
    private String genParamsJson;

    private String errorMessage;

    /** 创作需求原文（画廊卡片标题展示；重新生成时复用） */
    private String prompt;

    /** 草稿标记：false=成品 true=草稿（默认，新生成物进草稿区）。
     *  用 Boolean 而非 boolean：Lombok @Data 对 boolean isDraft 生成 isDraft() getter，
     *  Jackson 序列化为 "draft"；Boolean isDraft 生成 getIsDraft()，序列化为 "isDraft"，与前端对齐 */
    private Boolean isDraft;

    /** 终态完成/失败时间（ISO 格式）；前端展示任务耗时 */
    private String completedAt;

    /** 任务创建时间（ISO 格式）；前端计算耗时 */
    private String createdAt;
}