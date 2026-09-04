package com.dreamweaver.dto;

import lombok.Data;

import java.time.LocalDateTime;
import java.util.List;

/**
 * 小说预处理项目响应（不含原始 JSON 大字段之外的中间结构，segments 已反序列化为对象列表）。
 */
@Data
public class NovelProjectResponse {

    private Long id;
    private String projectName;
    private String novelText;
    private String chaptersJson;
    private String analysisJson;
    private List<NovelSegment> segments;
    private String visualStyle;
    private Long canvasProjectId;
    private String status;
    private String errorMessage;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
}
