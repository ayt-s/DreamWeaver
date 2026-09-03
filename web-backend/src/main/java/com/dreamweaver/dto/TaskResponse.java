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

    private String errorMessage;
}