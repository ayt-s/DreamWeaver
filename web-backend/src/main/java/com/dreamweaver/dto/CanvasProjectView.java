package com.dreamweaver.dto;

import com.dreamweaver.entity.CanvasProject;

import java.time.LocalDateTime;

/**
 * 画布项目视图（列表轻量版不含 JSON；详情版含 nodes/edges）。
 */
public record CanvasProjectView(
        Long id,
        String name,
        LocalDateTime updatedAt,
        String nodesJson,
        String edgesJson,
        Long parentId,
        String characterRefs,
        String sceneRefs) {

    public static CanvasProjectView of(CanvasProject p) {
        return new CanvasProjectView(
                p.getId(), p.getProjectName(), p.getUpdatedAt(),
                p.getNodesJson(), p.getEdgesJson(), p.getParentId(),
                p.getCharacterRefs(), p.getSceneRefs());
    }
}