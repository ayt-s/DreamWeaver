package com.dreamweaver.dto;

import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Size;
import lombok.Data;

/**
 * 小说预处理请求。
 * <p>projectName 与 novelText 为必填；targetSegments 与 secondsPerSegment 提供合理默认值。</p>
 */
@Data
public class NovelPreprocessRequest {

    @NotBlank(message = "projectName 不能为空")
    @Size(max = 64, message = "projectName 过长（≤64）")
    private String projectName;

    @NotBlank(message = "novelText 不能为空")
    @Size(max = 200000, message = "novelText 过长（≤200000）")
    private String novelText;

    @NotNull
    @Min(value = 4, message = "targetSegments 需在 4-12 之间")
    @Max(value = 12, message = "targetSegments 需在 4-12 之间")
    private Integer targetSegments = 6;

    @NotNull
    @Min(value = 4, message = "secondsPerSegment 需在 4-12 之间")
    @Max(value = 12, message = "secondsPerSegment 需在 4-12 之间")
    private Integer secondsPerSegment = 5;

    /**
     * 是否生成角色立绘。
     * <p>Phase 1 阶段仅接收并透传给 agent-service，UI 层不暴露；agent 侧当前管线尚未启用。</p>
     */
    @Deprecated(since = "Phase 2") // Phase 3 才实现定妆图功能
    private Boolean generateCharacterPortrait = false;
}
