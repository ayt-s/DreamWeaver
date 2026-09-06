package com.dreamweaver.dto;

import jakarta.validation.constraints.NotEmpty;
import jakarta.validation.constraints.NotNull;
import lombok.Data;

import java.util.List;
import java.util.Map;

/**
 * 批量重生中单个任务的配置：勾选哪些段重新生成 + 可选的提示词修改。
 */
@Data
public class BatchReworkItem {

    @NotNull(message = "taskId 不能为空")
    private Long taskId;

    @NotEmpty(message = "至少要勾选一段重新生成")
    private List<Integer> reworkIndices;

    /** 段索引(字符串 key) → 修改后的中文提示词；未提供或为空则沿用原提示词 */
    private Map<String, String> editedPrompts;
}
