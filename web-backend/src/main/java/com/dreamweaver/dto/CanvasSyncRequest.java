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

    /**
     * 覆盖确认：目标画布已有内容且与本次结果不一致时，不传 force 就**不写库**，
     * 返回 needConfirm 交前端确认；用户确认后带 force=true 重发。
     */
    private Boolean force;

    /**
     * 另存为新画布：保留现有画布（含手工调整），把本次分镜写进一张新画布。
     * 用于「改了分镜想重转，但舍不得上一版手工成果」的场景。
     */
    private Boolean saveAsNew;
}
