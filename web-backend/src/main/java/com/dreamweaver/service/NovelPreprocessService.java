package com.dreamweaver.service;

import com.dreamweaver.dto.NovelPreprocessRequest;
import com.dreamweaver.dto.NovelProjectResponse;
import com.dreamweaver.dto.NovelSegment;
import com.dreamweaver.dto.CanvasProjectView;
import com.dreamweaver.entity.NovelProject;

import java.util.List;

/**
 * 小说转漫剧服务：调用 agent-service 做预处理，落库并可与画布联动。
 */
public interface NovelPreprocessService {

    /**
     * 同步调用 agent-service 做小说预处理，成功后落库并把状态置为 ready。
     * 失败时状态置为 failed，errorMessage 记录原因。
     */
    NovelProject preprocess(Long userId, NovelPreprocessRequest req);

    /** 按 id 查询项目，不存在返回 null */
    NovelProject get(Long id);

    /** 按用户查询项目列表（轻量字段：不含 segments，避免大对象传输）。按 updatedAt 倒序，上限 50 */
    java.util.List<NovelProjectResponse> listByUser(Long userId);

    /** 响应视图（segments 已反序列化为对象） */
    NovelProjectResponse toResponse(NovelProject p);

    /** 更新分镜片段 JSON，返回最新视图 */
    NovelProjectResponse updateSegments(Long id, List<NovelSegment> segments);

    /** 把当前分镜同步到一张新画布项目（image/video/compose 网格布局） */
    CanvasProjectView saveToCanvas(Long novelProjectId);
}
