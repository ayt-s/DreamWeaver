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

    /** 生成产物 JSON（视频 URL 数组），前端轮询完成后展示视频用 */
    private String resultJson;

    private String errorMessage;
}