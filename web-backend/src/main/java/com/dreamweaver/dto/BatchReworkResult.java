package com.dreamweaver.dto;

import lombok.Data;

import java.util.List;

/**
 * 批量重生结果：汇总 + 每个任务的成功/失败详情。
 */
@Data
public class BatchReworkResult {

    private int total;
    private int success;
    private int failed;
    private List<BatchReworkItemResult> results;
}
