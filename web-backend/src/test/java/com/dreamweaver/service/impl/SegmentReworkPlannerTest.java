package com.dreamweaver.service.impl;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * {@link SegmentReworkPlanner} 的 L1 单测：纯 POJO（真 codec + 真 ObjectMapper），
 * 不启 Spring 上下文、不连库。
 *
 * <p>为什么这个类值得单测：这段逻辑原先藏在 {@code TaskServiceImpl.doReworkCore} 里、
 * 是 {@code private}，而它承载过两个**用户可见**的真实 bug：
 *
 * <ul>
 *   <li><b>索引错位</b>（提交 {@code 949cf41}）：历史产物与段数不匹配时按前缀对齐，
 *       会把产物挂到错误的段上 —— 表现为「重生 A 段，结果 B 段变了」，
 *       更坏的情况是 A 段永远没被重生</li>
 *   <li><b>缺产物的段被静默跳过</b>：把「没有历史产物」当成「可复用」→ 用户点了重生
 *       但那段压根没重新生成</li>
 * </ul>
 *
 * <p>核心不变式：**未勾选的段只有在确实拿到历史产物时才复用；拿不到就必须补进重生列表。**
 */
class SegmentReworkPlannerTest {

    /** `U+XXXX` 转义的前缀。用拼接构造：Java 的 U+ 是词法预处理，源码里直接写会编译报错。 */
    private static final String UNICODE_ESCAPE_PREFIX = "\\" + "u";

    private final TaskJsonCodec codec = new TaskJsonCodec(new ObjectMapper());
    private final SegmentReworkPlanner planner = new SegmentReworkPlanner(codec);

    // ---------------------------------------------------------------- 构造工具

    /** 造 3 段配置：每段带 prompt / prompt_en / camera */
    private String segmentsJson(int n) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < n; i++) {
            if (i > 0) {
                sb.append(',');
            }
            sb.append("{\"prompt\":\"描述").append(i).append("\",")
              .append("\"prompt_en\":\"en").append(i).append("\",")
              .append("\"seconds\":\"5\"}");
        }
        return sb.append(']').toString();
    }

    private String urlsJson(String... urls) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < urls.length; i++) {
            if (i > 0) {
                sb.append(',');
            }
            sb.append('"').append(urls[i]).append('"');
        }
        return sb.append(']').toString();
    }

    private SegmentReworkPlanner.ReworkPlan planVideo(String segJson, String resultJson,
                                                      List<Integer> rework,
                                                      Map<String, String> edited) {
        return planner.plan(1L, segJson, "text_video", resultJson, null, rework, edited);
    }

    private SegmentReworkPlanner.ReworkPlan planImage(String segJson, String imageUrls,
                                                      List<Integer> rework,
                                                      Map<String, String> edited) {
        return planner.plan(1L, segJson, "text_image", null, imageUrls, rework, edited);
    }

    private static Object get(Map<String, Object> seg, String key) {
        return seg.get(key);
    }

    // ------------------------------------------------- 分支 1：勾选段 → 重生

    @Test
    @DisplayName("勾选段：清掉 prompt_en 与两个复用字段，并用新描述覆盖 prompt")
    void selectedSegmentsBecomeRegenerate() {
        String segs = segmentsJson(3);
        String result = urlsJson("/v1/files/s/final.mp4", "http://cdn/0.mp4", "http://cdn/1.mp4",
                "http://cdn/2.mp4");

        var plan = planVideo(segs, result, List.of(1), Map.of("1", "新中文描述"));

        Map<String, Object> seg1 = plan.segments().get(1);
        assertEquals("新中文描述", get(seg1, "prompt"), "新描述必须覆盖 prompt");
        // 中文改了 → 旧译文失效，必须删掉让 agent 重新翻译
        assertNull(get(seg1, "prompt_en"), "改了描述就必须清 prompt_en 重新翻译");
        assertNull(get(seg1, SegmentReworkPlanner.KEY_EXISTING_VIDEO));
        assertNull(get(seg1, SegmentReworkPlanner.KEY_EXISTING_IMAGE));

        assertEquals(List.of(1), plan.effectiveRework().stream().toList());
        assertEquals(2, plan.reusedCount());
    }

    @Test
    @DisplayName("勾选段：未提供新描述时不覆盖 prompt（只做重生标记）")
    void selectedSegmentWithoutEditedPromptKeepsOriginalPrompt() {
        var plan = planVideo(segmentsJson(3),
                urlsJson("/v1/files/s/final.mp4", "http://cdn/0.mp4", "http://cdn/1.mp4",
                        "http://cdn/2.mp4"),
                List.of(0), null);

        assertEquals("描述0", get(plan.segments().get(0), "prompt"));
        assertNull(get(plan.segments().get(0), "prompt_en"),
                "重生段一律要重新翻译（即便描述没改，避免拿到过期译文）");
    }

    // ------------------------------------- 分支 2：未勾选 + 有产物 → 复用

    @Test
    @DisplayName("未勾选段 + 有历史产物 → 写回 existing_video_url（视频任务）")
    void unselectedVideoSegmentReusesExistingUrl() {
        var plan = planVideo(segmentsJson(3),
                urlsJson("/v1/files/s/final.mp4", "http://cdn/0.mp4", "http://cdn/1.mp4",
                        "http://cdn/2.mp4"),
                List.of(1), null);

        assertEquals("http://cdn/0.mp4", get(plan.segments().get(0),
                SegmentReworkPlanner.KEY_EXISTING_VIDEO));
        assertEquals("http://cdn/2.mp4", get(plan.segments().get(2),
                SegmentReworkPlanner.KEY_EXISTING_VIDEO));
        // 视频任务不该写图片复用键（写错键等于没复用）
        assertNull(get(plan.segments().get(0), SegmentReworkPlanner.KEY_EXISTING_IMAGE));
        // 复用段的 prompt_en 保留（没有重新翻译的必要）
        assertNotNull(get(plan.segments().get(0), "prompt_en"));
    }

    @Test
    @DisplayName("未勾选段 + 有历史产物 → 写回 existing_image_url（图片任务，产物在 image_urls）")
    void unselectedImageSegmentReusesImageUrl() {
        var plan = planImage(segmentsJson(2),
                urlsJson("http://cdn/a.png", "http://cdn/b.png"),
                List.of(0), null);

        assertEquals("http://cdn/b.png", get(plan.segments().get(1),
                SegmentReworkPlanner.KEY_EXISTING_IMAGE));
        assertNull(get(plan.segments().get(1), SegmentReworkPlanner.KEY_EXISTING_VIDEO),
                "图片任务不能写视频复用键");
    }

    @Test
    @DisplayName("★协作契约：视频 result_json 首元素是拼接成片时，必须被剔除后仍与段索引对齐")
    void finalVideoIsDroppedSoIndexesStayAligned() {
        // result_json = [final.mp4, seg0, seg1, seg2] 而段数是 3。
        // 若不剔除成片（TaskJsonCodec.parseResultUrls 的职责），
        // seg0..2 会被错位对齐到 seg1..3 → 复用错段。
        var plan = planVideo(segmentsJson(3),
                urlsJson("/v1/files/s/final.mp4", "http://cdn/0.mp4", "http://cdn/1.mp4",
                        "http://cdn/2.mp4"),
                List.of(0), null);

        assertEquals("http://cdn/1.mp4", get(plan.segments().get(1),
                SegmentReworkPlanner.KEY_EXISTING_VIDEO));
        assertEquals("http://cdn/2.mp4", get(plan.segments().get(2),
                SegmentReworkPlanner.KEY_EXISTING_VIDEO));
        assertTrue(plan.effectiveRework().equals(java.util.Set.of(0))
                        || plan.effectiveRework().stream().noneMatch(i -> i == 1 || i == 2),
                "1/2 段有产物可复用，不该进重生列表：" + plan.effectiveRework());
    }

    @Test
    @DisplayName("拼接失败：result_json 无成片（全为分段）→ 全部可复用，且不误剔除")
    void listWithoutFinalVideoIsFullyReusable() {
        var plan = planVideo(segmentsJson(2), urlsJson("http://cdn/0.mp4", "http://cdn/1.mp4"),
                List.of(0), null);

        assertEquals("http://cdn/1.mp4", get(plan.segments().get(1),
                SegmentReworkPlanner.KEY_EXISTING_VIDEO));
        assertEquals(java.util.Set.of(0), plan.effectiveRework());
    }

    // ------------------------- 分支 3：未勾选 + 无产物 → 自动补进重生列表

    @Test
    @DisplayName("未勾选段 + 无历史产物 → 自动补进 effectiveRework（否则那段永远修不好）")
    void unselectedSegmentWithoutProductIsAutoReworked() {
        // 产物数组：成片 + seg0 + **空占位** + seg2 —— agent 生成失败的段会以空串占位
        // 保持索引对齐（见 session_store / video.py），所以空占位必须被识别为「无产物」。
        var plan = planVideo(segmentsJson(3),
                urlsJson("/v1/files/s/final.mp4", "http://cdn/0.mp4", "", "http://cdn/2.mp4"),
                List.of(0), null);

        assertEquals(java.util.Set.of(0, 1), plan.effectiveRework(),
                "1 段只有空占位 → 必须自动补进重生列表（0 是用户勾选的）");
        assertNull(get(plan.segments().get(1), "prompt_en"));
        assertNull(get(plan.segments().get(1), SegmentReworkPlanner.KEY_EXISTING_VIDEO));
        // 2 段有产物 → 复用，不在重生列表
        assertEquals("http://cdn/2.mp4", get(plan.segments().get(2),
                SegmentReworkPlanner.KEY_EXISTING_VIDEO));
        assertEquals(1, plan.reusedCount());
    }

    @Test
    @DisplayName("产物比段少（索引越界）→ 越界段按「无产物」处理，不会 IndexOutOfBounds")
    void outOfRangeIndexIsTreatedAsMissingProduct() {
        var plan = planVideo(segmentsJson(4), urlsJson("/v1/files/s/final.mp4", "http://cdn/0.mp4"),
                List.of(0), null);

        assertEquals(java.util.Set.of(0, 1, 2, 3), plan.effectiveRework());
        assertEquals(0, plan.reusedCount());
    }

    @Test
    @DisplayName("空串 / 空白 URL 视为「无产物」（历史生成失败的段会以空占位保持索引对齐）")
    void blankUrlCountsAsMissing() {
        var plan = planVideo(segmentsJson(3),
                urlsJson("/v1/files/s/final.mp4", "http://cdn/0.mp4", "", "  "),
                List.of(0), null);

        assertEquals(java.util.Set.of(0, 1, 2), plan.effectiveRework(),
                "空串与空白都该补重生；1 号有产物不该动");
    }

    // ------------------------------------------------ 其他契约

    @Test
    @DisplayName("产物为 null / 坏 JSON → 全部段补重生，不抛异常")
    void missingOrBrokenProductJsonReworksEverything() {
        for (String broken : new String[]{null, "", "   ", "不是 json", "{}"}) {
            var plan = planVideo(segmentsJson(2), broken, List.of(0), null);
            assertEquals(java.util.Set.of(0, 1), plan.effectiveRework());
            assertEquals(0, plan.reusedCount());
        }
    }

    @Test
    @DisplayName("段配置为空 → 空计划（不抛异常）")
    void emptySegmentsYieldsEmptyPlan() {
        for (String broken : new String[]{null, "", "[]"}) {
            var plan = planVideo(broken, urlsJson("http://cdn/0.mp4"), List.of(0), null);
            assertTrue(plan.segments().isEmpty());
            assertTrue(plan.effectiveRework().isEmpty());
            assertEquals("[]", plan.segmentsJson());
        }
    }

    @Test
    @DisplayName("segmentsJson 是可落库的 JSON，且与 segments 一致（agent 直接读它）")
    void segmentsJsonIsSerializedConsistently() {
        var plan = planVideo(segmentsJson(2),
                urlsJson("/v1/files/s/final.mp4", "http://cdn/0.mp4", "http://cdn/1.mp4"),
                List.of(0), Map.of("0", "改过的描述"));

        var parsed = codec.parseSegments(plan.segmentsJson());
        assertEquals(2, parsed.size());
        assertEquals("改过的描述", parsed.get(0).get("prompt"));
        assertEquals("http://cdn/1.mp4", parsed.get(1).get(SegmentReworkPlanner.KEY_EXISTING_VIDEO));
        // 中文不能被转义成 unicode 转义（agent 侧按原文使用）
        assertTrue(plan.segmentsJson().contains("改过的描述"));
        // 断言消息里也不能出现反斜杠+u 的相邻组合：
        // Java 的 unicode 转义是**词法预处理**，注释与字符串里的非法转义同样编译报错。
        assertFalse(plan.segmentsJson().contains(UNICODE_ESCAPE_PREFIX),
                "序列化不该产出 unicode 转义形式的中文");
    }

    @Test
    @DisplayName("漫剧 comic_video 也走图片产物字段（与文本生图同族）")
    void comicVideoUsesImageProductField() {
        var plan = planner.plan(1L, segmentsJson(2), "comic_video", null,
                urlsJson("http://cdn/a.png", "http://cdn/b.png"), List.of(0), null);

        assertEquals("http://cdn/b.png", get(plan.segments().get(1),
                SegmentReworkPlanner.KEY_EXISTING_IMAGE));
    }

    @Test
    @DisplayName("isImageTask：只有 text_image / comic_video 算图片类")
    void isImageTaskClassification() {
        assertTrue(SegmentReworkPlanner.isImageTask("text_image"));
        assertTrue(SegmentReworkPlanner.isImageTask("comic_video"));
        assertFalse(SegmentReworkPlanner.isImageTask("text_video"));
        assertFalse(SegmentReworkPlanner.isImageTask("image_video"));
        assertFalse(SegmentReworkPlanner.isImageTask(null));
    }

    @Test
    @DisplayName("不修改入参：原始段配置对象不被就地改写")
    void doesNotMutateInputSegments() {
        String segs = segmentsJson(2);
        planVideo(segs, urlsJson("/v1/files/s/final.mp4", "http://cdn/0.mp4", "http://cdn/1.mp4"),
                List.of(0), null);

        // 每次 plan 都重新 parse，所以原始 JSON 字符串必然不受影响；
        // 这里锁的是「不该把复用字段写回源数组」这一契约
        var reparsed = codec.parseSegments(segs);
        assertNull(reparsed.get(1).get(SegmentReworkPlanner.KEY_EXISTING_VIDEO));
    }
}
