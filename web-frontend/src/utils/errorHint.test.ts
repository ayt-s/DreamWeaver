/**
 * `errorHint`：把后端诊断文案翻成「下一步该怎么办」（P3）。
 *
 * 锁定三件事：
 * 1. 常见失败类都能给出**具体动作**（而不是「请稍后重试」这种等于没说的）；
 * 2. **不改写后端文案** —— 只是追加建议，所以断言里检查的是「有没有指出动作」；
 * 3. 不命中就返回 null（宁可不提示，也不给废话）。
 */
import { describe, expect, it } from 'vitest';

import { errorHint } from './errorHint';

describe('errorHint', () => {
  it('网络/代理解析失败 → 让用户去查网络', () => {
    // 这正是 2026-09-17 任务 53 的文案（后端 errors.py 改过之后的措辞）
    const hint = errorHint('连接生成平台失败（网络或代理不通），请检查网络后重试');
    expect(hint).toMatch(/网络/);
    expect(hint).toMatch(/重新生成/);
  });

  it('平台排队/限流 → 告诉用户等一会儿，而不是以为坏了', () => {
    const hint = errorHint('视频提交所有 provider 都失败（已尝试 1 个 provider × 6 次重试）：[intl] 平台限流(429)');
    expect(hint).toMatch(/排队|等/);
  });

  it('超时 → 明确说「不是故障」', () => {
    const hint = errorHint('生成平台响应超时（平台排队/限流时常见），请稍后重试');
    expect(hint).toMatch(/不是故障|排队/);
  });

  it('质检未通过 → 指向「按段重生」而不是整条重跑', () => {
    expect(errorHint('2/4 镜未通过质检：画面模糊')).toMatch(/按段重生/);
  });

  it('拼接失败 → 说明分段仍可用', () => {
    expect(errorHint('多镜拼接失败，分段仍可下载')).toMatch(/分段/);
  });

  it('Agent 服务不可用 → 指向要检查的服务（这条来自 Java 侧，语义不同）', () => {
    expect(errorHint('Agent 服务暂不可用，请稍后重试')).toMatch(/8000/);
  });

  it('密钥问题 → 指向 .env', () => {
    expect(errorHint('API 密钥无效或已过期，请联系管理员')).toMatch(/env|API Key/);
  });

  it('不认识的失败原因 → 返回 null（不渲染，也不给废话）', () => {
    expect(errorHint('某种没见过的错误 XYZ')).toBeNull();
  });

  it('空值安全', () => {
    expect(errorHint(null)).toBeNull();
    expect(errorHint(undefined)).toBeNull();
    expect(errorHint('')).toBeNull();
  });

  it('★ 建议里不含「请稍后重试」这类空话（必须给出可执行动作）', () => {
    const samples = [
      '连接生成平台失败（网络或代理不通），请检查网络后重试',
      '平台队列繁忙，请稍后重试',
      '生成平台响应超时（平台排队/限流时常见），请稍后重试',
      '账户余额不足，请充值后重试',
    ];
    for (const s of samples) {
      const hint = errorHint(s);
      expect(hint).toBeTruthy();
      expect(hint).not.toBe('请稍后重试');
      expect((hint as string).length).toBeGreaterThan(6);
    }
  });
});
