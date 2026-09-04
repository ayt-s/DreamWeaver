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

    private String errorMessage;

    /** 创作需求原文（画廊卡片标题展示；重新生成时复用） */
    private String prompt;
}