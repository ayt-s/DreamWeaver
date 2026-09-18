package com.dreamweaver.service.impl;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 「运行期复用键不得进落库基线」的回归测试（2026-09-19）。
 *
 * <h3>为什么值得写</h3>
 *
 * 「按段重生」为了让未勾选的段省额度，会往段里塞 {@code existing_video_url} /
 * {@code existing_image_url}，agent 收到就 {@code continue}（不提交 agnes）。
 * 但 {@code doReworkCore} 此前把**同一份**带键的 JSON 也落了库 —— 而 {@code segments_json}
 * 是「全量重生」的输入源，于是：
 *
 * <pre>
 *   用户点「全量重生」→ agent 见到现成的 existing_video_url → 复用旧视频、不重新生成
 *   ⇒ 只有上次勾选过的段真的重跑，其余段静默不动，UI 却显示「重生中」
 * </pre>
 *
 * 实测污染：库中 id=54/38/36/29 的 {@code segments_json} 均含 {@code existing_video_url}
 * （LOCATE 命中 987 / 1628 / 837 / 456）。
 */
class SegmentReworkReuseKeyTest {

    private final TaskJsonCodec codec = new TaskJsonCodec(new ObjectMapper());
    private final SegmentReworkPlanner planner = new SegmentReworkPlanner(codec);

    @Test
    @DisplayName("stripReuseKeys：剥掉两个复用键，其余字段一字不改")
    void stripsReuseKeysOnly() {
        String json = """
                [{"image_url":"https://x/1.png","prompt":"海边","seconds":4,"aspect_ratio":"16:9",
                  "existing_video_url":"https://x/old1.mp4","existing_image_url":"https://x/old1.png"},
                 {"image_url":"https://x/2.png","prompt":"屋内","seconds":4,"aspect_ratio":"16:9"}]""";
        String out = planner.stripReuseKeys(json);

        assertFalse(out.contains("existing_video_url"), "复用键必须剥掉，否则污染全量重生基线");
        assertFalse(out.contains("existing_image_url"));
        // 业务字段一个不少
        assertTrue(out.contains("https://x/1.png"));
        assertTrue(out.contains("https://x/old1.mp4") == false, "它是被剥掉的键的值，不该再出现");
        assertTrue(out.contains("海边") && out.contains("屋内"));
        assertTrue(out.contains("16:9"));
    }

    @Test
    @DisplayName("stripReuseKeys：幂等 —— 再剥一次结果相同")
    void stripIsIdempotent() {
        String json = """
                [{"prompt":"a","existing_video_url":"https://x/a.mp4"}]""";
        String once = planner.stripReuseKeys(json);
        assertEquals(once, planner.stripReuseKeys(once));
    }

    @Test
    @DisplayName("stripReuseKeys：空值原样返回（不能把 null 变成字面量）")
    void passthroughForBlank() {
        assertNull(planner.stripReuseKeys(null));
        assertEquals("", planner.stripReuseKeys(""));
        assertEquals("   ", planner.stripReuseKeys("   "));
    }
}
