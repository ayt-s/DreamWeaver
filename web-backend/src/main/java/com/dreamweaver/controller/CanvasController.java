package com.dreamweaver.controller;

import com.dreamweaver.common.CommonResult;
import com.dreamweaver.dto.CanvasProjectRequest;
import com.dreamweaver.dto.CanvasProjectView;
import com.dreamweaver.entity.CanvasProject;
import com.dreamweaver.service.CanvasProjectService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

/**
 * 无限画布项目接口：按自定义名称创建/保存/加载/删除画布。
 */
@RestController
@RequestMapping("/api/canvas")
@RequiredArgsConstructor
public class CanvasController {

    private final CanvasProjectService projectService;

    private static final long DEFAULT_USER_ID = 1L;

    /** 创建项目（空画布） */
    @PostMapping
    public CommonResult<CanvasProjectView> createProject(@RequestBody CanvasProjectRequest req) {
        if (req.getName() == null || req.getName().isBlank()) {
            throw new IllegalArgumentException("项目名称不能为空");
        }
        CanvasProject p = projectService.createProject(req.getName().trim(), DEFAULT_USER_ID);
        return CommonResult.ok(CanvasProjectView.of(p));
    }

    /** 项目列表（轻量，无 JSON，供下拉选择） */
    @GetMapping
    public CommonResult<List<CanvasProjectView>> listProjects() {
        return CommonResult.ok(projectService.listProjects(DEFAULT_USER_ID).stream()
                .map(CanvasProjectView::of)
                .toList());
    }

    /** 加载项目完整内容（含 nodes/edges JSON） */
    @GetMapping("/{id}")
    public CommonResult<CanvasProjectView> getProject(@PathVariable Long id) {
        CanvasProject p = projectService.getProject(id, DEFAULT_USER_ID);
        if (p == null) {
            throw new IllegalArgumentException("画布项目不存在: " + id);
        }
        return CommonResult.ok(CanvasProjectView.of(p));
    }

    /** 保存画布内容 / 重命名（只更新非空字段） */
    @PutMapping("/{id}")
    public CommonResult<CanvasProjectView> saveProject(
            @PathVariable Long id, @RequestBody CanvasProjectRequest req) {
        CanvasProject p = projectService.saveProject(
                id, DEFAULT_USER_ID, req.getName(), req.getNodesJson(), req.getEdgesJson());
        return CommonResult.ok(CanvasProjectView.of(p));
    }

    /** 删除项目 */
    @DeleteMapping("/{id}")
    public CommonResult<Void> deleteProject(@PathVariable Long id) {
        projectService.deleteProject(id, DEFAULT_USER_ID);
        return CommonResult.ok(null);
    }
}