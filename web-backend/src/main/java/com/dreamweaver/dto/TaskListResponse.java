package com.dreamweaver.dto;

import lombok.Data;

import java.util.List;

/**
 * 任务分页列表响应 dto（画廊页：分页 + genType 筛选）。
 */
@Data
public class TaskListResponse {

    private List<TaskResponse> list;

    private long total;

    private int page;

    private int size;
}