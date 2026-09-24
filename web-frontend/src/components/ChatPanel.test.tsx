/**
 * ChatPanel 工具轨迹渲染（2026-09-23 后端修复的配套展示）。
 *
 * 后端此前的轨迹里 `status` 恒为 `called`、`result` 恒为空 —— 前端那个「✗ 失败」分支
 * 是**死代码**。现在后端跨消息配对出真实结果，这里锁定三条展示语义：
 * 1. `ok` → ✓，`error` → ✗ 失败（红），`called` → 「…」（不能画成 ✓，那是在撒谎）；
 * 2. 悬浮详情要带上返回值摘要；
 * 3. 截断过的返回值必须显式标注，否则会被当成「工具就返回了这么点东西」。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ChatPanel from './ChatPanel';
import { agentChat, clearChatHistory, type ChatToolCall } from '../api/agent';

vi.mock('../api/agent', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/agent')>();
  return { ...actual, agentChat: vi.fn(), clearChatHistory: vi.fn() };
});

const mockedChat = vi.mocked(agentChat);
const mockedClear = vi.mocked(clearChatHistory);

function call(over: Partial<ChatToolCall>): ChatToolCall {
  return { tool_name: 'edit_prompt', args: { node_id: 'n1' }, status: 'ok', ...over };
}

async function sendAndGet(text: string) {
  render(<ChatPanel open onClose={() => {}} canvasId={7} hasProject dark={false} />);
  fireEvent.change(screen.getByPlaceholderText(/描述你想让 agent 做什么/), {
    target: { value: text },
  });
  fireEvent.click(screen.getByTitle('发送'));
  return screen.findByText('已处理');
}

describe('ChatPanel 工具轨迹', () => {
  beforeEach(() => {
    mockedChat.mockReset();
  });

  it('成功的工具显示 ✓ 并在悬浮里给出返回值', async () => {
    mockedChat.mockResolvedValue({
      reply: '已处理',
      tool_calls: [call({ status: 'ok', result: '{"saved": true}', truncated: false })],
    });
    await sendAndGet('改一下');

    const tag = screen.getByText('edit_prompt ✓');
    expect(tag).toHaveAttribute('title', expect.stringContaining('"saved": true'));
  });

  it('失败的工具显示 ✗ 失败（这是修复前永远看不到的状态）', async () => {
    mockedChat.mockResolvedValue({
      reply: '已处理',
      tool_calls: [call({ tool_name: 'delete_node', status: 'error', result: '节点 img9 不存在' })],
    });
    await sendAndGet('删掉 img9');

    const tag = screen.getByText('delete_node ✗ 失败');
    expect(tag).toHaveAttribute('title', expect.stringContaining('节点 img9 不存在'));
  });

  it('没有返回值的调用显示「…」而不是 ✓', async () => {
    mockedChat.mockResolvedValue({
      reply: '已处理',
      tool_calls: [call({ tool_name: 'concat_task', status: 'called' })],
    });
    await sendAndGet('拼一下');

    expect(screen.getByText('concat_task …')).toBeInTheDocument();
    expect(screen.queryByText('concat_task ✓')).toBeNull();
  });

  it('被截断的返回值显式标注', async () => {
    mockedChat.mockResolvedValue({
      reply: '已处理',
      tool_calls: [call({ tool_name: 'inspect_canvas', status: 'ok', result: '{"nodes":[', truncated: true })],
    });
    await sendAndGet('看下画布');

    expect(screen.getByText('inspect_canvas ✓')).toHaveAttribute(
      'title',
      expect.stringContaining('已截断'),
    );
  });
});

describe('ChatPanel 多轮对话 id（服务端真历史）', () => {
  beforeEach(() => {
    mockedChat.mockReset();
    mockedClear.mockReset();
    localStorage.clear();
  });

  it('发送时带上按画布生成的 conversation_id，同一画布保持一致', async () => {
    mockedChat.mockResolvedValue({ reply: '已处理', tool_calls: [] });
    render(<ChatPanel open onClose={() => {}} canvasId={42} hasProject dark={false} />);

    fireEvent.change(screen.getByPlaceholderText(/描述你想让 agent 做什么/), {
      target: { value: '改一下' },
    });
    fireEvent.click(screen.getByTitle('发送'));
    await screen.findByText('已处理');

    const sentId = mockedChat.mock.calls[0][3];
    expect(typeof sentId).toBe('string');
    expect(sentId).toBeTruthy();
    // 必须落到按画布分桶的 key 上：下次打开面板要沿用同一个 id，
    // 否则服务端历史对不上，多轮上下文等于白存
    expect(localStorage.getItem('dreamweaver:chat-conv:42')).toBe(sentId);
  });

  it('清空对话会同时清服务端历史并换新 id', async () => {
    mockedChat.mockResolvedValue({ reply: '已处理', tool_calls: [] });
    mockedClear.mockResolvedValue();
    render(<ChatPanel open onClose={() => {}} canvasId={7} hasProject dark={false} />);

    fireEvent.change(screen.getByPlaceholderText(/描述你想让 agent 做什么/), {
      target: { value: '改一下' },
    });
    fireEvent.click(screen.getByTitle('发送'));
    await screen.findByText('已处理');
    const oldId = mockedChat.mock.calls[0][3] as string;

    fireEvent.click(screen.getByTitle('清空对话'));

    await waitFor(() => expect(mockedClear).toHaveBeenCalledWith(oldId));
    expect(localStorage.getItem('dreamweaver:chat-conv:7')).not.toBe(oldId);
  });
});
