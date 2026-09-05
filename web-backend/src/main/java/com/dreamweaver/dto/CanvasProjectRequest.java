package com.dreamweaver.dto;

import com.dreamweaver.entity.CanvasProject;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;
import lombok.Data;

/**
 * 画布项目请求/响应 DTO。
 * 创建只传 name；保存可传 name（重命名）/ nodesJson / edgesJson 任意组合。
 */
@Data
public class CanvasProjectRequest {

    /** 项目名称。创建必填（controller 校验）；保存时可选（重命名） */
    @Size(max = 64, message = "项目名称过长（≤64）")
    private String name;

    /** React Flow 节点数组 JSON 字符串 */
    private String nodesJson;

    /** React Flow 连线数组 JSON 字符串 */
    private String edgesJson;

    /** 父项目 ID（子项目指向父项目，NULL 表示顶级） */
    private Long parentId;

    /** 角色锚定图 JSON（{"角色名": "url"}） */
    private String characterRefs;

    /** 场景锚定图 JSON（{"场景名": "url"}） */
    private String sceneRefs;
}