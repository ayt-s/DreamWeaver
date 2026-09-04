package com.dreamweaver.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import lombok.Data;

import java.time.LocalDateTime;

/**
 * 无限画布项目（对应 canvas_project 表）。
 * 按自定义名称保存节点/连线 JSON，供画布页多项目切换与持久化。
 */
@Data
@TableName("canvas_project")
public class CanvasProject {

    @TableId(type = IdType.AUTO)
    private Long id;

    /** 项目名称（用户自定义） */
    private String projectName;

    private Long userId;

    /** React Flow 节点数组 JSON 字符串 */
    private String nodesJson;

    /** React Flow 连线数组 JSON 字符串 */
    private String edgesJson;

    private LocalDateTime createdAt;

    private LocalDateTime updatedAt;
}