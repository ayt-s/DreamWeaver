package com.dreamweaver.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.dreamweaver.entity.CanvasProject;
import com.dreamweaver.mapper.CanvasProjectMapper;
import com.dreamweaver.service.CanvasProjectService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;

/**
 * 画布项目服务实现。所有权校验：所有查询/写操作强制按 userId 隔离（当前单用户默认 1）。
 */
@Slf4j
@Service
@RequiredArgsConstructor
public class CanvasProjectServiceImpl implements CanvasProjectService {

    private final CanvasProjectMapper mapper;

    @Override
    @Transactional
    public CanvasProject createProject(String name, Long userId) {
        CanvasProject p = new CanvasProject();
        p.setProjectName(name);
        p.setUserId(userId);
        mapper.insert(p);
        log.info("画布项目创建: id={} name={}", p.getId(), name);
        return p;
    }

    @Override
    public List<CanvasProject> listProjects(Long userId) {
        return mapper.selectList(new LambdaQueryWrapper<CanvasProject>()
                .eq(CanvasProject::getUserId, userId)
                .orderByDesc(CanvasProject::getUpdatedAt));
    }

    @Override
    public CanvasProject getProject(Long id, Long userId) {
        CanvasProject p = mapper.selectById(id);
        if (p == null || !userId.equals(p.getUserId())) {
            return null;
        }
        return p;
    }

    @Override
    @Transactional
    public CanvasProject saveProject(Long id, Long userId, String name,
                                     String nodesJson, String edgesJson) {
        CanvasProject existing = getProject(id, userId);
        if (existing == null) {
            throw new IllegalArgumentException("画布项目不存在: " + id);
        }
        CanvasProject patch = new CanvasProject();
        patch.setId(id);
        if (name != null && !name.isBlank()) {
            patch.setProjectName(name.trim());
        }
        if (nodesJson != null) {
            patch.setNodesJson(nodesJson);
        }
        if (edgesJson != null) {
            patch.setEdgesJson(edgesJson);
        }
        mapper.updateById(patch);
        log.info("画布项目保存: id={} {}", id, name == null ? "" : "rename=" + name);
        return getProject(id, userId);
    }

    @Override
    @Transactional
    public void deleteProject(Long id, Long userId) {
        if (getProject(id, userId) == null) {
            throw new IllegalArgumentException("画布项目不存在: " + id);
        }
        mapper.deleteById(id);
        log.info("画布项目删除: id={}", id);
    }
}