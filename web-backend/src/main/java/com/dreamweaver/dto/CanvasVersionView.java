package com.dreamweaver.dto;

import com.dreamweaver.entity.CanvasProject;

import java.time.LocalDateTime;

/**
 * 画布版本视图（轻量）：只含轮询需要的版本号与时间，**不含 nodes/edges JSON**。
 *
 * <p>为什么单独一个 DTO：画布页要每隔几秒探测「别处是否改过这张画布」（助手会写画布、
 * 另一个标签页也可能在编辑）。复用 {@link CanvasProjectView} 会把 12KB+ 的 nodesJson
 * 一并拉回来 —— 探活就变成了拖库。</p>
 */
public record CanvasVersionView(Long id, Integer version, LocalDateTime updatedAt) {

    public static CanvasVersionView of(CanvasProject p) {
        return new CanvasVersionView(p.getId(), p.getVersion(), p.getUpdatedAt());
    }
}
