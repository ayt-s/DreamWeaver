package com.dreamweaver.service;

import com.dreamweaver.entity.CanvasProject;

import java.util.List;

/**
 * 无限画布项目服务。
 * 画布页按自定义名称保存/加载节点与连线，支持多项目切换。
 */
public interface CanvasProjectService {

    /** 创建项目（空画布），返回落库实体 */
    CanvasProject createProject(String name, Long userId);

    /** 项目列表（轻量：不带 nodes/edges JSON，供下拉选择） */
    List<CanvasProject> listProjects(Long userId);

    /** 加载项目完整内容（含 nodes/edges JSON），不存在返回 null */
    CanvasProject getProject(Long id, Long userId);

    /** 保存画布内容 / 重命名（只更新非空字段），返回最新实体 */
    CanvasProject saveProject(Long id, Long userId, String name, String nodesJson, String edgesJson);

    /** 删除项目 */
    void deleteProject(Long id, Long userId);
}