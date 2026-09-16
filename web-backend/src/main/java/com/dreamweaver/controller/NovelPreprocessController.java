package com.dreamweaver.controller;

import com.dreamweaver.common.CommonResult;
import com.dreamweaver.dto.CanvasProjectView;
import com.dreamweaver.dto.NovelPreprocessRequest;
import com.dreamweaver.dto.NovelProjectResponse;
import com.dreamweaver.dto.NovelSegmentUpdateRequest;
import com.dreamweaver.service.NovelPreprocessService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.DeleteMapping;
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

    /** 项目列表（按用户，按更新时间倒序，轻量字段不含 segments） */
    @GetMapping
    public CommonResult<java.util.List<NovelProjectResponse>> list() {
        return CommonResult.ok(service.listByUser(DEFAULT_USER_ID));
    }

    /** 更新分镜片段 JSON */
    @PutMapping("/{id}/segments")
    public CommonResult<NovelProjectResponse> updateSegments(
            @PathVariable Long id, @RequestBody NovelSegmentUpdateRequest req) {
        return CommonResult.ok(service.updateSegments(id, req.getSegments()));
    }

    /**
     * 删除小说项目记录（列表里的清理入口）。
     * 只删本记录；它生成的画布项目不受影响。
     */
    @DeleteMapping("/{id}")
    public CommonResult<Void> delete(@PathVariable Long id) {
        service.delete(id);
        return CommonResult.ok(null);
    }

    /**
     * 同步到画布项目（幂等：项目已绑定画布则复用更新，不再重复新建）。
     * body 可选：携带角色/场景锚定图 → 落库到 canvas_project.character_refs/scene_refs。
     */
    @PostMapping("/{id}/to-canvas")
    public CommonResult<com.dreamweaver.dto.ToCanvasResult> toCanvas(
            @PathVariable Long id,
            @RequestBody(required = false) com.dreamweaver.dto.CanvasSyncRequest body) {
        return CommonResult.ok(service.saveToCanvas(
                id,
                body == null ? null : body.getCharacterRefs(),
                body == null ? null : body.getSceneRefs(),
                body != null && Boolean.TRUE.equals(body.getForce()),
                body != null && Boolean.TRUE.equals(body.getSaveAsNew())));
    }
}
