package com.dreamweaver.dto;

import lombok.Data;

/**
 * 批量重生中单个任务的结果。
 */
@Data
public class BatchReworkItemResult {

    private Long taskId;
    private boolean success;
    private String message;
    private TaskResponse task;
}
