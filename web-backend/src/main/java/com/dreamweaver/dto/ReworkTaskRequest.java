package com.dreamweaver.dto;

import jakarta.validation.constraints.NotEmpty;
import lombok.Data;

import java.util.List;
import java.util.Map;

/**
 * 穿帮段重新生成请求：勾选若干段（可附带修改后的提示词）重新生成并重新拼接。
 * 未勾选的段直接复用已有视频，不重复消耗模型额度。
 */
@Data
public class ReworkTaskRequest {

    /** 需要重新生成的段索引（0-based，对应提交时的 segments 顺序） */
    @NotEmpty(message = "至少要勾选一段重新生成")
    private List<Integer> reworkIndices;

    /** 段索引(字符串 key) → 修改后的中文提示词；未提供或为空则沿用原提示词 */
    private Map<String, String> editedPrompts;
}
