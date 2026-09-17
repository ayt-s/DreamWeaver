package com.dreamweaver.service.impl;

import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.baomidou.mybatisplus.core.metadata.TableInfoHelper;
import com.dreamweaver.entity.Task;
import com.dreamweaver.mapper.TaskMapper;
import com.dreamweaver.service.impl.TaskServiceImpl.SourceFilter;
import org.apache.ibatis.builder.MapperBuilderAssistant;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * {@link TaskServiceImpl#listTasks} 的来源/状态过滤。
 *
 * <p><b>为什么值得测：</b>「查哪些行」错了**不会报错** —— 只会静默少返回（面板看起来本来就是空的）
 * 或多返回（画布素材挤掉作品）。而它同时服务画廊、画布素材面板、画布作品面板三条链路，
 * 加参数时最容易把画廊的老语义改坏。
 *
 * <p>纯 Mockito，不启 Spring、不连 MySQL。
 */
@ExtendWith(MockitoExtension.class)
class TaskServiceImplListFilterTest {

    @Mock
    private TaskMapper taskMapper;

    /**
     * LambdaQueryWrapper 解析 lambda → 列名需要 MyBatis-Plus 的 TableInfo 缓存，
     * 那个缓存平时由 MapperScan 在启动时建立；纯单测里得自己初始化一次，
     * 否则断言 SQL 时抛 {@code can not find lambda cache for this entity}。
     */
    @BeforeAll
    static void initMybatisPlusTableInfo() {
        TableInfoHelper.initTableInfo(new MapperBuilderAssistant(new MybatisConfiguration(), ""), Task.class);
    }

    /** 只注入 mapper，其余依赖本用例用不到（@RequiredArgsConstructor 按字段顺序） */
    private TaskServiceImpl service() {
        return new TaskServiceImpl(null, taskMapper, null, null, null, null, null);
    }

    @Test
    @DisplayName("source 取值 → 过滤形态：asset / work / 不传（含大小写与未知值）")
    void sourceFilterDecisionTable() {
        // 画布素材面板：显式要素材
        assertEquals(SourceFilter.ASSETS_ONLY, TaskServiceImpl.sourceFilterOf("asset", true));
        assertEquals(SourceFilter.ASSETS_ONLY, TaskServiceImpl.sourceFilterOf("ASSET", false));
        // 画布作品面板：显式要作品（= 排除素材，与画廊默认同义）
        assertEquals(SourceFilter.EXCLUDE_ASSETS, TaskServiceImpl.sourceFilterOf("work", true));
        // 画廊：不传 source → 老语义（默认排除画布素材，否则画廊会被一键文生图的素材刷屏）
        assertEquals(SourceFilter.EXCLUDE_ASSETS, TaskServiceImpl.sourceFilterOf(null, false));
        assertEquals(SourceFilter.EXCLUDE_ASSETS, TaskServiceImpl.sourceFilterOf("  ", false));
        // 老面板：不传 source 但 includeAssets=true → 素材 + 作品都要
        assertEquals(SourceFilter.NO_FILTER, TaskServiceImpl.sourceFilterOf(null, true));
        // 未知取值不抛错，退回按 includeAssets 的老语义（否则前端传错参数会「什么都查不到」）
        assertEquals(SourceFilter.NO_FILTER, TaskServiceImpl.sourceFilterOf("bogus", true));
        assertEquals(SourceFilter.EXCLUDE_ASSETS, TaskServiceImpl.sourceFilterOf("bogus", false));
    }

    @Test
    @DisplayName("source=asset + status=completed 会真的进 SQL（不只是解构出来）")
    void wrapperCarriesSourceAndStatus() {
        when(taskMapper.selectCount(any())).thenReturn(0L);
        when(taskMapper.selectList(any())).thenReturn(List.of());

        service().listTasks(1, 12, "text_image", null, true, "completed", "asset", null);

        ArgumentCaptor<LambdaQueryWrapper<Task>> captor = ArgumentCaptor.forClass(LambdaQueryWrapper.class);
        verify(taskMapper).selectList(captor.capture());
        String sql = captor.getValue().getSqlSegment();
        assertTrue(sql.contains("source"), sql);
        assertTrue(sql.contains("status"), sql);
        assertTrue(captor.getValue().getParamNameValuePairs().containsValue("canvas_asset"), sql);
    }

    @Test
    @DisplayName("不传 source 时画廊查询不含 source 条件（老行为不劣化）")
    void wrapperOmitsSourceForGallery() {
        when(taskMapper.selectCount(any())).thenReturn(0L);
        when(taskMapper.selectList(any())).thenReturn(List.of());

        service().listTasks(1, 10, null, null, false, null, null, null);

        ArgumentCaptor<LambdaQueryWrapper<Task>> captor = ArgumentCaptor.forClass(LambdaQueryWrapper.class);
        verify(taskMapper).selectList(captor.capture());
        String sql = captor.getValue().getSqlSegment();
        assertTrue(sql.contains("source"), sql);          // 画廊默认仍要排除素材
        assertFalse(sql.contains("status"), sql);         // 不传 status 就不该有 status 条件
        assertFalse(sql.contains("prompt"), sql);         // 不传 keyword 就不该有 prompt 条件
    }

    @Test
    @DisplayName("LIKE 通配符必须被转义（否则搜「%」会把全部记录都匹配上）")
    void escapeLikeEscapesWildcards() {
        assertEquals("陈浔", TaskServiceImpl.escapeLike("陈浔"));
        assertEquals("100\\%", TaskServiceImpl.escapeLike("100%"));
        assertEquals("a\\_b", TaskServiceImpl.escapeLike("a_b"));
        // 反斜杠必须**先**转义，否则后面的 % 会被二次转义错误
        assertEquals("a\\\\b", TaskServiceImpl.escapeLike("a\\b"));
        assertEquals("\\\\\\%", TaskServiceImpl.escapeLike("\\%"));
    }

    @Test
    @DisplayName("keyword 进 SQL 且带通配符包裹与转义（搜 % 不该等于搜全部）")
    void wrapperCarriesEscapedKeyword() {
        when(taskMapper.selectCount(any())).thenReturn(0L);
        when(taskMapper.selectList(any())).thenReturn(List.of());

        service().listTasks(1, 12, null, null, true, null, null, "陈浔 100%");

        ArgumentCaptor<LambdaQueryWrapper<Task>> captor = ArgumentCaptor.forClass(LambdaQueryWrapper.class);
        verify(taskMapper).selectList(captor.capture());
        String sql = captor.getValue().getSqlSegment();
        assertTrue(sql.contains("prompt"), sql);
        // MP 的 like 把参数包成 %val%，所以我们期望的是「包一层外层通配符 + 内层已转义」
        assertTrue(
                captor.getValue().getParamNameValuePairs().containsValue("%陈浔 100\\%%"),
                captor.getValue().getParamNameValuePairs().toString());
    }
}
