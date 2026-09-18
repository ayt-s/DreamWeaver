import { afterEach, describe, expect, it, vi } from 'vitest';
import { editImage } from './imageEdit';

/** 替换全局 fetch，返回 [fetchMock, ...] —— 只断言**发出去的载荷**与错误传播。 */
function stubFetch(body: unknown, init: { ok?: boolean; status?: number } = {}) {
  const fn = vi.fn().mockResolvedValue({
    ok: init.ok ?? true,
    status: init.status ?? 200,
    json: async () => body,
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe('editImage（POST /v1/images/edit）', () => {
  it('成功时返回新图 URL，并按后端字段名发载荷', async () => {
    const f = stubFetch({ code: 0, message: 'ok', data: { urls: ['https://cdn/fixed.png'] } });

    const urls = await editImage('https://cdn/src.png', '只保留一头黑牛，其余保持不变', {
      ratio: '16:9',
    });

    expect(urls).toEqual(['https://cdn/fixed.png']);
    const [path, opts] = f.mock.calls[0] as [string, RequestInit];
    expect(path).toBe('/v1/images/edit');
    expect(opts.method).toBe('POST');
    const payload = JSON.parse(String(opts.body));
    // 字段名必须与 ImageEditRequest 一致（snake_case）—— 写成 imageUrl 会被 pydantic 判缺字段
    expect(payload).toEqual({
      image_url: 'https://cdn/src.png',
      instruction: '只保留一头黑牛，其余保持不变',
      ratio: '16:9',
      count: 1,
    });
  });

  it('count 可覆盖（一次多出几张修正结果）', async () => {
    const f = stubFetch({ code: 0, data: { urls: ['a', 'b'] } });
    await editImage('https://cdn/src.png', '去掉多余道具', { count: 2 });
    expect(JSON.parse(String((f.mock.calls[0][1] as RequestInit).body)).count).toBe(2);
  });

  it('后端 code!=0 时抛出后端文案 —— 用户主动点的操作不能静默失败', async () => {
    // 这是与质检（失败就静默降级）的关键区别：修正失败必须让用户看见
    stubFetch({ code: 1, message: '修正失败：上游 429' });
    await expect(editImage('https://cdn/src.png', '改一处')).rejects.toThrow('修正失败：上游 429');
  });

  it('非 JSON 响应体（如网关 502 页面）也要给出可读错误', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        json: async () => {
          throw new Error('not json');
        },
      }),
    );
    await expect(editImage('https://cdn/src.png', '改一处')).rejects.toThrow('HTTP 502');
  });

  it('响应缺 data.urls 时返回空数组（由调用方判空并提示）', async () => {
    stubFetch({ code: 0, message: 'ok' });
    await expect(editImage('https://cdn/src.png', '改一处')).resolves.toEqual([]);
  });
});
