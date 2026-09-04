package com.dreamweaver.dto;

import lombok.Data;

import java.util.List;

/**
 * PUT /api/novel/{id}/segments 请求体。
 */
@Data
public class NovelSegmentUpdateRequest {

    private List<NovelSegment> segments;
}
