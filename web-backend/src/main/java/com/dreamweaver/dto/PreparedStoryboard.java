package com.dreamweaver.dto;

import lombok.Data;

import java.util.List;
import java.util.Map;

/**
 * agent-service 返回的预处理结果结构（PreparedStoryboard）。
 * Java 侧接收时字段按 camelCase 命名，与 agent 端 snake_case 通过 Jackson 属性映射对齐。
 */
@Data
public class PreparedStoryboard {

    private String novelSummary;

    /** 角色 -> 描述 */
    private Map<String, String> characters;

    /** 主要场景列表 */
    private List<String> scenes;

    /** 分镜片段 */
    private List<NovelSegment> segments;

    private Integer totalSegments;

    private Integer totalDurationSeconds;
}
