package com.dreamweaver.service.impl;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.conditions.update.LambdaUpdateWrapper;
import com.dreamweaver.dto.CanvasProjectView;
import com.dreamweaver.dto.SaveCanvasResult;
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
    public SaveCanvasResult saveProject(Long id, Long userId, String name,
                                     String nodesJson, String edgesJson,
                                     String characterRefs, String sceneRefs,
                                     Integer expectedVersion) {
        CanvasProject existing = getProject(id, userId);
        if (existing == null) {
            throw new IllegalArgumentException("画布项目不存在: " + id);
        }
        // 用条件更新代替 updateById：expectedVersion 非空时，版本不符就匹配不到任何行，
        // 于是「读-改-写」之间的并发修改不会像以前那样被静默覆盖。
        LambdaUpdateWrapper<CanvasProject> w = new LambdaUpdateWrapper<CanvasProject>()
                .eq(CanvasProject::getId, id);
        if (expectedVersion != null) {
            w.eq(CanvasProject::getVersion, expectedVersion);
        }
        if (name != null && !name.isBlank()) {
            w.set(CanvasProject::getProjectName, name.trim());
        }
        if (nodesJson != null) {
            w.set(CanvasProject::getNodesJson, nodesJson);
        }
        if (edgesJson != null) {
            w.set(CanvasProject::getEdgesJson, edgesJson);
        }
        if (characterRefs != null) {
            w.set(CanvasProject::getCharacterRefs, characterRefs);
        }
        if (sceneRefs != null) {
            w.set(CanvasProject::getSceneRefs, sceneRefs);
        }
        w.setSql("version = version + 1");
        int rows = mapper.update(null, w);

        SaveCanvasResult res = new SaveCanvasResult();
        if (rows == 0 && expectedVersion != null) {
            // 版本不符 = 画布已被别处修改。顺带把服务端现状返回，前端可直接重载，省一次 GET
            CanvasProject now = getProject(id, userId);
            res.setConflict(true);
            res.setServerVersion(now == null ? null : now.getVersion());
            res.setServerNodesJson(now == null ? null : now.getNodesJson());
            res.setServerEdgesJson(now == null ? null : now.getEdgesJson());
            log.warn("画布保存版本冲突: id={} 期望版本={} 服务端版本={}",
                    id, expectedVersion, now == null ? null : now.getVersion());
            return res;
        }
        CanvasProject fresh = getProject(id, userId);
        res.setCanvas(CanvasProjectView.of(fresh));
        log.info("画布项目保存: id={} {} version={}", id, name == null ? "" : "rename=" + name,
                fresh == null ? null : fresh.getVersion());
        return res;
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