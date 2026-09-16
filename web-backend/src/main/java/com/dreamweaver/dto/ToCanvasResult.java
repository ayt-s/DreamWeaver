package com.dreamweaver.dto;

import lombok.Data;

import java.time.LocalDateTime;

/**
 * 「转入画布」的结果。
 *
 * <p>{@code needConfirm=true} 表示目标画布已有内容、且与本次将要写入的不一致（= 被改过），
 * 此时**不写库**，把上下文交给前端做三选一：覆盖 / 另存为新画布 / 取消。</p>
 */
@Data
public class ToCanvasResult {

    /** 成功转入（或另存）后的画布；needConfirm=true 时为 null */
    private CanvasProjectView canvas;

    /** true = 需用户确认，本次未写库 */
    private boolean needConfirm;

    /** 以下仅在 needConfirm=true 时有值，用于生成确认文案 */
    private Long canvasId;
    private String canvasName;
    private Integer canvasNodeCount;
    private LocalDateTime canvasUpdatedAt;
    private Integer incomingNodeCount;
}
