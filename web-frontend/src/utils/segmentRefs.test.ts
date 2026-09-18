/**
 * P2-9：**面板显示的第 N 张 == 实际提交时 agent 写进提示词的第 N 张**。
 *
 * 数据是**真实画布**（项目 40「长生烬9-17-1」v16）：3 个角色锚 + 5 个场景锚 + 6 段分镜提示词，
 * 原样粘进来的，不是造的样例。
 * `EXPECTED_REFS` / `EXPECTED_INDEXES` 是**用 agent 侧真实规则跑出来的**：
 * 组装数组 → `app/utils/prompting.py:build_reference_bindings(bindings, refs)`
 * → 解析它写出的 `<Picture N>`。所以「前端显示编号」与「agent 实际编号」是**跨栈核对**，
 * 不是前端自己算给自己看（生成脚本：`D:\cli_ai\hermes-temp\gen_segmentrefs_test.py`）。
 *
 * 缺陷背景：面板以前按**全局**编号显示（锚定图一律从 2 起 +1），而提交时每段只带这一段
 * 真正用到的锚定图（P0-3）→ 真实编号逐段不同。实测这一张画布 6 段**逐段都不一致**：
 * 例如「山村茅屋废墟…」面板写「图片 7」，agent 实际写的是 `<Picture 4>`。
 */
import { describe, expect, it } from 'vitest';
import { MAX_REF_PICTURES } from './anchors';
import {
  bindingBadge,
  bindingIndexesInSegments,
  pictureIndexOf,
  segmentRefImages,
  type SegmentForRefs,
} from './segmentRefs';

const CHAR_URLS: Record<string, string> = {
  "陈浔": "https://platform-outputs.agnes-ai.space/images/t2i/task_0fYgErQnS7evC8VcxoAqbHwugv5GUqez/output_d9445ca3ace7459bbdfeea17dd2b013c.png",
  "大黑牛": "https://cos-platform-outputs.agnes-ai.cn/images/t2i/task_ggBPg0pyevf5hlXqEjzLfaZAodFcsWFe/output_7bb652e1e387459a8931ecb3c82b7d64.png",
  "小黑子": "https://cos-platform-outputs.agnes-ai.cn/images/t2i/task_j9wlaheoCOB3d7dlzeULUYMJzKFlzNAh/output_2f50e7b0e5c14317b5d352f307ac27b2.png"
};

const SCENE_URLS: Record<string, string> = {
  "小山村山坡，清晨，阳光斜照，绿意盎然，少年叼着狗尾草躺坐，身旁黑牛盘腿而坐，微风拂过万木倾伏，氛围宁静中带着一丝窃喜": "https://cos-platform-outputs.agnes-ai.cn/images/t2i/task_bKowEoFDOh7gODqchdPZr5TJOLTSB1XV/output_6d00ea76fcc640198c9d60b272e4a6de.png",
  "山村茅草屋，午后，灶火正旺，炊烟袅袅升起，屋内土墙斑驳，木桌简陋，地面铺干草，弥漫着饭香，气氛温暖质朴": "https://platform-outputs.agnes-ai.space/images/t2i/task_Z6Yc03Tkd4g5DVrn8h06MYDItAUY5IUu/output_2ca0c041e87d403da29583522e616359.png",
  "山村茅屋废墟，黄昏，余烬未熄，黑烟仍在上升，焦黑的木梁倒在地上，一人一牛跪坐门外，神情呆滞绝望，氛围凄凉无助": "https://cos-platform-outputs.agnes-ai.cn/images/t2i/task_rScQfYWiUlh0Z2Dq7ni2PFlrX7iJlmwl/output_c7093c40dad8495abcbba371cdc04a04.png",
  "村外山洞，日间，洞内灰褐色石壁，地面铺满干草，角落堆放干柴和简陋木器，洞口透进自然光，清冷中透着安稳": "https://platform-outputs.agnes-ai.space/images/t2i/task_5tAlQUQNpsiF2wldlvQDQnXG2bO3en8U/output_aa0b02e331b64d56b183ef61e2fc4de4.png",
  "村民宴会场地，夜晚，红布覆盖长桌，铜锅热菜，村民围坐，火把照亮夜空，粗布衣裳色彩混杂，热闹中带着朴实的人间烟火气": "https://platform-outputs.agnes-ai.space/images/t2i/task_btqZ17SMBLT3vbUIt485664pfZ8tVa5o/output_3d556691494f4a21b45f2606c05001fd.png"
};

/** 6 段真实的 `image_url` + 提示词（提交载荷里的形状） */
const SEGMENTS: SegmentForRefs[] = [
  {
    "image_url": "https://cos-platform-outputs.agnes-ai.cn/images/t2i/task_Z6tGLd1ZCAfP59nbfoPwdcDPrkpk5xRq/output_fcc0eda678904e61a5bfd1d20a0e5b4f.png",
    "prompt": "[角色锚] 陈浔；[主体动作] 陈浔突然闻到焦味；[场景] 山村茅草屋，黄昏，余烬未熄，黑烟仍在上升，焦黑的木梁倒在地上，一人一牛跪坐门外，神情呆滞绝望，氛围凄凉无助，大黑牛（通体漆黑的灵兽牛）；[镜头] 中景推近，背景烟雾缭绕；[风格] 3D 写实国漫、虚幻 5 渲染、颗粒感，情绪惊慌绝望。严禁面部特写；严禁人物居中占比较大；4K 超高清；16:9 画幅；主体清晰居中；四周安全边距；无字幕无水印；无文字乱码。"
  },
  {
    "image_url": "https://platform-outputs.agnes-ai.space/images/t2i/task_YsV9v0YE6p4qvW3GVY7rEdFpS4qZ0CzQ/output_a08f02b9de3143b8b645d20d11ecfb0f.png",
    "prompt": "[角色锚] 陈浔；[主体动作] 少年陈浔叼着狗尾草躺坐山坡；[场景] 小山村山坡，清晨，阳光斜照，绿意盎然，少年叼着狗尾草躺坐，身旁黑牛盘腿而坐，微风拂过万木倾伏，氛围宁静中带着一丝窃喜；[镜头] 中景缓慢横移，从远景山林推向少年与黑牛，主体居中，前景有野草虚化；[风格] 3D 写实国漫、虚幻 5 渲染、颗粒感，情绪宁静窃喜。严禁面部特写；严禁人物居中占比较大；4K 超高清；16:9 画幅；主体清晰居中；四周安全边距；无字幕无水印；无文字乱码。"
  },
  {
    "image_url": "https://platform-outputs.agnes-ai.space/images/t2i/task_HpKDEMQ0oXF3kFC1o4RCsLXBdGX3Tn43/output_ed222055947d46d58ed2a822f3e0f066.png",
    "prompt": "[角色锚] 陈浔、小黑子；[主体动作] 山村茅屋前；[场景] 山村茅草屋，午后，灶火正旺，炊烟袅袅升起，屋内土墙斑驳，木桌简陋，地面铺干草，弥漫着饭香，气氛温暖质朴，大黑牛（通体漆黑的灵兽牛）；[镜头] 全景固定，画面三分线构图，前景有门框遮挡，人物居中偏右；[风格] 3D 写实国漫、虚幻 5 渲染、颗粒感，情绪温暖感激。严禁面部特写；严禁人物居中占比较大；4K 超高清；16:9 画幅；主体清晰居中；四周安全边距；无字幕无水印；无文字乱码。"
  },
  {
    "image_url": "https://platform-outputs.agnes-ai.space/images/t2i/task_nt4yehu8TOKjGsJa3i2s18JEgbvJV3Eu/output_5294bdb02635413bbb69283a4ae23ea0.png",
    "prompt": "[角色锚] 陈浔；[主体动作] 村外山洞内；[场景] 村外山洞，日间，洞内灰褐色石壁，地面铺满干草，角落堆放干柴和简陋木器，洞口透进自然光，清冷中透着安稳，大黑牛（通体漆黑的灵兽牛）；[镜头] 中景环绕，绕少年与黑牛缓慢旋转，展现山洞环境与进食动作；[风格] 3D 写实国漫、虚幻 5 渲染、颗粒感，情绪狡黠满足。严禁面部特写；严禁人物居中占比较大；4K 超高清；16:9 画幅；主体清晰居中；四周安全边距；无字幕无水印；无文字乱码。"
  },
  {
    "image_url": "https://cos-platform-outputs.agnes-ai.cn/images/t2i/task_CKwGaB26HhBo7on1CuzfSmMnISHxIKnt/output_d0273355839c4fe78444b797683e33c0.png",
    "prompt": "[角色锚] 陈浔；[主体动作] 火势渐缓后；[场景] 山村茅屋废墟，黄昏，余烬未熄，黑烟仍在上升，焦黑的木梁倒在地上，一人一牛跪坐门外，神情呆滞绝望，氛围凄凉无助，大黑牛（通体漆黑的灵兽牛）；[镜头] 全景固定，画面三分线构图，废墟占画面三分之二，人物居左下角；[风格] 3D 写实国漫、虚幻 5 渲染、颗粒感，情绪绝望无助。严禁面部特写；严禁人物居中占比较大；4K 超高清；16:9 画幅；主体清晰居中；四周安全边距；无字幕无水印；无文字乱码。"
  },
  {
    "image_url": "https://platform-outputs.agnes-ai.space/images/t2i/task_9HRyaixwZIhyQH58NQKTn9QmZkQIMiNo/output_ae7da3a5393b41bfba7ebce464ce31e9.png",
    "prompt": "[角色锚] 陈浔；[主体动作] 茅屋内；[场景] 山村茅草屋，午后，灶火正旺，炊烟袅袅升起，屋内土墙斑驳，木桌简陋，地面铺干草，弥漫着饭香，气氛温暖质朴，大黑牛（通体漆黑的灵兽牛）；[镜头] 近景跟拍，从米袋推向少年面部，再摇至黑牛表情，主体始终在画面中央；[风格] 3D 写实国漫、虚幻 5 渲染、颗粒感，情绪神秘坚定。严禁面部特写；严禁人物居中占比较大；4K 超高清；16:9 画幅；主体清晰居中；四周安全边距；无字幕无水印；无文字乱码。"
  }
];

/** 各段**真实**参考图数组（agent 侧拿到的那一份） */
const EXPECTED_REFS: string[][] = [
  [
    "https://cos-platform-outputs.agnes-ai.cn/images/t2i/task_Z6tGLd1ZCAfP59nbfoPwdcDPrkpk5xRq/output_fcc0eda678904e61a5bfd1d20a0e5b4f.png",
    "https://platform-outputs.agnes-ai.space/images/t2i/task_0fYgErQnS7evC8VcxoAqbHwugv5GUqez/output_d9445ca3ace7459bbdfeea17dd2b013c.png",
    "https://cos-platform-outputs.agnes-ai.cn/images/t2i/task_ggBPg0pyevf5hlXqEjzLfaZAodFcsWFe/output_7bb652e1e387459a8931ecb3c82b7d64.png",
    "https://cos-platform-outputs.agnes-ai.cn/images/t2i/task_rScQfYWiUlh0Z2Dq7ni2PFlrX7iJlmwl/output_c7093c40dad8495abcbba371cdc04a04.png"
  ],
  [
    "https://platform-outputs.agnes-ai.space/images/t2i/task_YsV9v0YE6p4qvW3GVY7rEdFpS4qZ0CzQ/output_a08f02b9de3143b8b645d20d11ecfb0f.png",
    "https://platform-outputs.agnes-ai.space/images/t2i/task_0fYgErQnS7evC8VcxoAqbHwugv5GUqez/output_d9445ca3ace7459bbdfeea17dd2b013c.png",
    "https://cos-platform-outputs.agnes-ai.cn/images/t2i/task_ggBPg0pyevf5hlXqEjzLfaZAodFcsWFe/output_7bb652e1e387459a8931ecb3c82b7d64.png",
    "https://cos-platform-outputs.agnes-ai.cn/images/t2i/task_bKowEoFDOh7gODqchdPZr5TJOLTSB1XV/output_6d00ea76fcc640198c9d60b272e4a6de.png"
  ],
  [
    "https://platform-outputs.agnes-ai.space/images/t2i/task_HpKDEMQ0oXF3kFC1o4RCsLXBdGX3Tn43/output_ed222055947d46d58ed2a822f3e0f066.png",
    "https://platform-outputs.agnes-ai.space/images/t2i/task_0fYgErQnS7evC8VcxoAqbHwugv5GUqez/output_d9445ca3ace7459bbdfeea17dd2b013c.png",
    "https://cos-platform-outputs.agnes-ai.cn/images/t2i/task_ggBPg0pyevf5hlXqEjzLfaZAodFcsWFe/output_7bb652e1e387459a8931ecb3c82b7d64.png",
    "https://cos-platform-outputs.agnes-ai.cn/images/t2i/task_j9wlaheoCOB3d7dlzeULUYMJzKFlzNAh/output_2f50e7b0e5c14317b5d352f307ac27b2.png",
    "https://platform-outputs.agnes-ai.space/images/t2i/task_Z6Yc03Tkd4g5DVrn8h06MYDItAUY5IUu/output_2ca0c041e87d403da29583522e616359.png"
  ],
  [
    "https://platform-outputs.agnes-ai.space/images/t2i/task_nt4yehu8TOKjGsJa3i2s18JEgbvJV3Eu/output_5294bdb02635413bbb69283a4ae23ea0.png",
    "https://platform-outputs.agnes-ai.space/images/t2i/task_0fYgErQnS7evC8VcxoAqbHwugv5GUqez/output_d9445ca3ace7459bbdfeea17dd2b013c.png",
    "https://cos-platform-outputs.agnes-ai.cn/images/t2i/task_ggBPg0pyevf5hlXqEjzLfaZAodFcsWFe/output_7bb652e1e387459a8931ecb3c82b7d64.png",
    "https://platform-outputs.agnes-ai.space/images/t2i/task_5tAlQUQNpsiF2wldlvQDQnXG2bO3en8U/output_aa0b02e331b64d56b183ef61e2fc4de4.png"
  ],
  [
    "https://cos-platform-outputs.agnes-ai.cn/images/t2i/task_CKwGaB26HhBo7on1CuzfSmMnISHxIKnt/output_d0273355839c4fe78444b797683e33c0.png",
    "https://platform-outputs.agnes-ai.space/images/t2i/task_0fYgErQnS7evC8VcxoAqbHwugv5GUqez/output_d9445ca3ace7459bbdfeea17dd2b013c.png",
    "https://cos-platform-outputs.agnes-ai.cn/images/t2i/task_ggBPg0pyevf5hlXqEjzLfaZAodFcsWFe/output_7bb652e1e387459a8931ecb3c82b7d64.png",
    "https://cos-platform-outputs.agnes-ai.cn/images/t2i/task_rScQfYWiUlh0Z2Dq7ni2PFlrX7iJlmwl/output_c7093c40dad8495abcbba371cdc04a04.png"
  ],
  [
    "https://platform-outputs.agnes-ai.space/images/t2i/task_9HRyaixwZIhyQH58NQKTn9QmZkQIMiNo/output_ae7da3a5393b41bfba7ebce464ce31e9.png",
    "https://platform-outputs.agnes-ai.space/images/t2i/task_0fYgErQnS7evC8VcxoAqbHwugv5GUqez/output_d9445ca3ace7459bbdfeea17dd2b013c.png",
    "https://cos-platform-outputs.agnes-ai.cn/images/t2i/task_ggBPg0pyevf5hlXqEjzLfaZAodFcsWFe/output_7bb652e1e387459a8931ecb3c82b7d64.png",
    "https://platform-outputs.agnes-ai.space/images/t2i/task_Z6Yc03Tkd4g5DVrn8h06MYDItAUY5IUu/output_2ca0c041e87d403da29583522e616359.png"
  ]
];

/** 各锚定图的逐段真实编号（直接来自 agent 的 `<Picture N>`） */
const EXPECTED_INDEXES: Record<string, number[]> = {
  "char:陈浔": [
    2
  ],
  "char:大黑牛": [
    3
  ],
  "scene:山村茅屋废墟，黄昏，余烬未熄，黑烟仍在上升，焦黑的木梁倒在地上，一人一牛跪坐门外，神情呆滞绝望，氛围凄凉无助": [
    4
  ],
  "scene:小山村山坡，清晨，阳光斜照，绿意盎然，少年叼着狗尾草躺坐，身旁黑牛盘腿而坐，微风拂过万木倾伏，氛围宁静中带着一丝窃喜": [
    4
  ],
  "char:小黑子": [
    4
  ],
  "scene:山村茅草屋，午后，灶火正旺，炊烟袅袅升起，屋内土墙斑驳，木桌简陋，地面铺干草，弥漫着饭香，气氛温暖质朴": [
    4,
    5
  ],
  "scene:村外山洞，日间，洞内灰褐色石壁，地面铺满干草，角落堆放干柴和简陋木器，洞口透进自然光，清冷中透着安稳": [
    4
  ],
  "scene:村民宴会场地，夜晚，红布覆盖长桌，铜锅热菜，村民围坐，火把照亮夜空，粗布衣裳色彩混杂，热闹中带着朴实的人间烟火气": []
};

/** 面板**以前**按全局编号显示的值（与真实编号不同的那些） */
const OLD_GLOBAL: Array<{ key: string; global: number; real: number[] }> = [
  {
    "key": "scene:山村茅屋废墟，黄昏，余烬未熄，黑烟仍在上升，焦黑的木梁倒在地上，一人一牛跪坐门外，神情呆滞绝望，氛围凄凉无助",
    "global": 7,
    "real": [
      4
    ]
  },
  {
    "key": "scene:小山村山坡，清晨，阳光斜照，绿意盎然，少年叼着狗尾草躺坐，身旁黑牛盘腿而坐，微风拂过万木倾伏，氛围宁静中带着一丝窃喜",
    "global": 5,
    "real": [
      4
    ]
  },
  {
    "key": "scene:山村茅草屋，午后，灶火正旺，炊烟袅袅升起，屋内土墙斑驳，木桌简陋，地面铺干草，弥漫着饭香，气氛温暖质朴",
    "global": 6,
    "real": [
      4,
      5
    ]
  },
  {
    "key": "scene:村外山洞，日间，洞内灰褐色石壁，地面铺满干草，角落堆放干柴和简陋木器，洞口透进自然光，清冷中透着安稳",
    "global": 8,
    "real": [
      4
    ]
  }
];

describe('逐段参考图编号（P2-9：面板显示的编号 == 实际提交的编号）', () => {
  it('每段组装出的参考图数组与 agent 侧拿到的一致（含 5 张截断）', () => {
    SEGMENTS.forEach((seg, i) => {
      expect(segmentRefImages(seg, CHAR_URLS, SCENE_URLS)).toEqual(EXPECTED_REFS[i]);
    });
  });

  it('★ 同一个锚定图在所有段落里的真实编号 == agent 写进提示词的编号', () => {
    for (const [key, expected] of Object.entries(EXPECTED_INDEXES)) {
      const url = urlOf(key);
      expect(bindingIndexesInSegments(url, SEGMENTS, CHAR_URLS, SCENE_URLS), key).toEqual(expected);
    }
  });

  it('★ 这些差异真的存在过：全局编号 ≠ 真实编号（不是假想的问题）', () => {
    expect(OLD_GLOBAL.length).toBeGreaterThan(0);
    for (const row of OLD_GLOBAL) {
      expect(row.real, row.key).not.toContain(row.global);
    }
  });

  it('编号就是 url 在**这一段数组**里的下标 + 1（唯一判据）', () => {
    EXPECTED_REFS.forEach((refs, i) => {
      refs.forEach((url, k) => {
        expect(pictureIndexOf(refs, url)).toBe(k + 1);
        expect(segmentRefImages(SEGMENTS[i], CHAR_URLS, SCENE_URLS)[k]).toBe(url);
      });
    });
    // 本段没有这张图就是 null（不编一个像样的数字）
    expect(pictureIndexOf(EXPECTED_REFS[0], 'https://cdn.example.com/not-in-this-segment.png')).toBeNull();
  });

  it('★ 某段没有自己的首帧图时，编号整体前移一位（照抄全局编号必错）', () => {
    const chen = CHAR_URLS['陈浔'];
    const withoutFrame: SegmentForRefs[] = [
      { image_url: '', prompt: SEGMENTS[0].prompt },
      SEGMENTS[0],
    ];

    expect(bindingIndexesInSegments(chen, withoutFrame, CHAR_URLS, SCENE_URLS)).toEqual([1, 2]);
    // 全局编号（旧的显示口径）会说它是 2 —— 在那一段就是错的
    expect(bindingIndexesInSegments(chen, withoutFrame, CHAR_URLS, SCENE_URLS)).not.toEqual([2]);
  });

  it('一段都没用到它 → 空数组（面板显示「未随段发送」，不撒谎）', () => {
    const unused = Object.entries(SCENE_URLS).find(
      ([name]) => EXPECTED_INDEXES[`scene:${name}`].length === 0,
    );
    expect(unused, '这份真实数据里应当有「一段都没命中」的场景锚').toBeTruthy();
    const [, url] = unused as [string, string];
    expect(bindingIndexesInSegments(url, SEGMENTS, CHAR_URLS, SCENE_URLS)).toEqual([]);

    const badge = bindingBadge([], 9, true);
    expect(badge.text).toBe('未随段发送');
    expect(badge.title).toContain('不会随段发给模型');
    expect(bindingBadge([], 9, false).text).toBe('图片 9'); // 还没有可提交段落时才显示预估位
  });

  it('徽标文案：各段一致 → `图片 N`；逐段不同 → `逐段 …`（不挑一个数字去骗用户）', () => {
    expect(bindingBadge([3], 2, true).text).toBe('图片 3');
    const multi = bindingBadge([2, 4], 2, true);
    expect(multi.text).toBe('逐段 2/4');
    expect(multi.title).toContain('逐段编号不同');
  });

  it('上限：每段最多 5 张（含本段首帧图），截断后不再有第 6 张', () => {
    const many: Record<string, string> = {};
    for (let i = 0; i < 8; i += 1) many[`角色${i}`] = `https://cdn.example.com/p${i}.png`;
    const prompt = Object.keys(many).join('、');
    const refs = segmentRefImages({ image_url: 'https://cdn.example.com/first.png', prompt }, many, {});
    expect(refs).toHaveLength(MAX_REF_PICTURES);
    expect(refs[0]).toBe('https://cdn.example.com/first.png');
  });
});

/** `char:名字` / `scene:描述` → url */
function urlOf(key: string): string {
  const idx = key.indexOf(':');
  const kind = key.slice(0, idx);
  const name = key.slice(idx + 1);
  const url = kind === 'char' ? CHAR_URLS[name] : SCENE_URLS[name];
  expect(url, key).toBeTruthy();
  return url;
}
