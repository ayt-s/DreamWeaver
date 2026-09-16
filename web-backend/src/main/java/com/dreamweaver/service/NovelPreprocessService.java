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

    /**
     * 删除小说项目记录。
     * <p>⚠️ 只删本表记录：它生成的画布项目（canvas_project_id 指向的那张）**不受影响** ——
     * 画布可能已被手工编辑过，级联删除会误伤。清理画布请到无限画布页删。</p>
     */
    void delete(Long id);

    /**
     * 把当前分镜同步到画布项目（image/video/compose 网格布局）。
     * <p>覆盖保护：目标画布已有内容且与本次结果不一致（= 被改过）时，未传
     * force/saveAsNew 就**不写库**，返回 needConfirm 交前端确认。</p>
     * <p>幂等：项目已绑定画布时复用更新，不再每次新建（此前点 N 次「转入画布」
     * 就在库里留下 N 个同名画布项目）。锚定图随本次一并落库。</p>
     */
    com.dreamweaver.dto.ToCanvasResult saveToCanvas(Long novelProjectId, String characterRefs,
            String sceneRefs, boolean force, boolean saveAsNew);
}
