package com.dreamweaver.controller;

import com.dreamweaver.common.CommonResult;
import com.dreamweaver.dto.CanvasProjectView;
import com.dreamweaver.dto.NovelPreprocessRequest;
import com.dreamweaver.dto.NovelProjectResponse;
import com.dreamweaver.dto.NovelSegmentUpdateRequest;
import com.dreamweaver.service.NovelPreprocessService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 小说转漫剧：预处理 / 查询 / 更新分镜 / 同步到画布。
 */
@RestController
@RequestMapping("/api/novel")
@RequiredArgsConstructor
public class NovelPreprocessController {

    private static final long DEFAULT_USER_ID = 1L;

    private final NovelPreprocessService service;

    /** 预处理：同步调用 agent-service，返回项目实体（含 segments） */
    @PostMapping("/preprocess")
    public CommonResult<NovelProjectResponse> preprocess(@Valid @RequestBody NovelPreprocessRequest req) {
        return CommonResult.ok(service.toResponse(service.preprocess(DEFAULT_USER_ID, req)));
    }

    /** 查询项目 */
    @GetMapping("/{id}")
    public CommonResult<NovelProjectResponse> get(@PathVariable Long id) {
        return CommonResult.ok(service.toResponse(service.get(id)));
    }

    /** 更新分镜片段 JSON */
    @PutMapping("/{id}/segments")
    public CommonResult<NovelProjectResponse> updateSegments(
            @PathVariable Long id, @RequestBody NovelSegmentUpdateRequest req) {
        return CommonResult.ok(service.updateSegments(id, req.getSegments()));
    }

    /** 同步到画布项目 */
    @PostMapping("/{id}/to-canvas")
    public CommonResult<CanvasProjectView> toCanvas(@PathVariable Long id) {
        return CommonResult.ok(service.saveToCanvas(id));
    }
}
