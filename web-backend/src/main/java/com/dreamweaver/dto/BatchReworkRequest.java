package com.dreamweaver.dto;

import jakarta.validation.Valid;
import jakarta.validation.constraints.NotEmpty;
import lombok.Data;

import java.util.List;

/**
 * 批量穿帮段重新生成请求。
 * 一次提交多个任务的段重生，每个任务独立执行，一个失败不影响其他。
 */
@Data
public class BatchReworkRequest {

    @NotEmpty(message = "批量重生列表不能为空")
    @Valid
    private List<BatchReworkItem> items;
}
