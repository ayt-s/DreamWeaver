package com.dreamweaver.dto;

import lombok.Data;

/**
 * 小说分镜同步到画布的补充参数（锚定图）。
 * <p>锚定图此前只通过前端 URL query + localStorage 传递，刷新画布或换设备就丢；
 * 落库到 canvas_project.character_refs / scene_refs 后才是持久的。</p>
 */
@Data
public class CanvasSyncRequest {

    /** 角色锚定图 JSON：{"角色名": "图片URL"}；null/空 = 不动已有值 */
    private String characterRefs;

    /** 场景锚定图 JSON：{"场景名": "图片URL"}；null/空 = 不动已有值 */
    private String sceneRefs;
}
