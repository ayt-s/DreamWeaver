/**
 * QcReportBlock（A7）：逐镜质检结果渲染。
 *
 * 锁定三条语义：
 * 1. `qc === null` 表示「**没跑**质检」（图片任务 / 合成视频）—— 不渲染任何东西。
 *    若把它渲染成「0/0 通过」或「全部通过」，用户会把「未检查」误读成「检查合格」。
 * 2. 失败时逐镜列出原因，镜号按 1-based 展示（与后端 error 文案一致）。
 * 3. 通过时不列明细，只显示 N/N。
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { QcReportBlock, TraceTimeline } from './TrajectoryPanel';
import type { QcReport } from '../api/agent';

function report(over: Partial<QcReport> = {}): QcReport {
  return {
    passed: true,
    total_shots: 3,
    failed_shots: [],
    shots: [
      { index: 0, passed: true, error: '' },
      { index: 1, passed: true, error: '' },
      { index: 2, passed: true, error: '' },
    ],
    reason: '',
    ...over,
  };
}

describe('QcReportBlock', () => {
  it('qc 为 null（没跑质检）时不渲染任何内容', () => {
    const { container } = render(<QcReportBlock qc={null} />);
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId('qc-report')).toBeNull();
  });

  it('全部通过时显示 N/N 且不列明细', () => {
    render(<QcReportBlock qc={report()} />);
    expect(screen.getByTestId('qc-report')).toHaveTextContent('质检：3/3 镜通过');
    expect(screen.queryByRole('listitem')).toBeNull();
  });

  it('有失败镜时逐镜列出原因，镜号为 1-based', () => {
    render(
      <QcReportBlock
        qc={report({
          passed: false,
          failed_shots: [1],
          reason: '1/3 镜未通过质检',
          shots: [
            { index: 0, passed: true, error: '' },
            {
              index: 1,
              passed: false,
              error: '画面质检未通过（模糊帧比例 90%）',
              duration: 5.0,
              duration_expected: 5,
            },
            { index: 2, passed: true, error: '' },
          ],
        })}
      />,
    );

    expect(screen.getByTestId('qc-report')).toHaveTextContent('质检：2/3 镜通过');
    // 第 1 镜通过不出现；失败的是 shot index 1 → 展示为「第 2 镜」
    expect(screen.getByText(/第 2 镜/)).toBeInTheDocument();
    expect(screen.queryByText(/第 1 镜/)).toBeNull();
    expect(screen.getByText(/模糊帧比例 90%/)).toBeInTheDocument();
  });

  it('缺失错误原因时回退为「未通过」，不渲染空白条目', () => {
    render(
      <QcReportBlock
        qc={report({ passed: false, failed_shots: [2], shots: [] })}
      />,
    );
    expect(screen.getByText(/第 3 镜：未通过/)).toBeInTheDocument();
  });

  it('多镜失败时全部列出', () => {
    render(
      <QcReportBlock
        qc={report({
          passed: false,
          failed_shots: [0, 2],
          shots: [
            { index: 0, passed: false, error: '产物缺失，未下载成功' },
            { index: 1, passed: true, error: '' },
            { index: 2, passed: false, error: '时长偏离（期望 5s，实测 12.0s）' },
          ],
        })}
      />,
    );
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    expect(screen.getByText(/产物缺失/)).toBeInTheDocument();
    expect(screen.getByText(/时长偏离/)).toBeInTheDocument();
  });
});

/**
 * TraceTimeline（批次 C3）：链路轨迹时间线。
 *
 * 存在的理由：面板原先只靠 SSE，刷新页面后事件列表为空 → 对一条**正在生成或已跑完**
 * 的任务显示一片空白。这份快照来自 REST 轮询，断线后照样能画。
 *
 * 锁定三条语义：
 * 1. 空数组不渲染（没轨迹就别占位）
 * 2. 逐镜条目（带 `#`）显示序号 —— 否则 10 镜任务只有一根长条，看不出卡在第几镜
 * 3. `elapsed_ms === 0` 是**进度标记**，不显示耗时（显示 `0ms` 会让人以为没花时间）
 */
describe('TraceTimeline（批次 C3）', () => {
  it('空数组不渲染任何内容', () => {
    const { container } = render(<TraceTimeline entries={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('节点级条目显示中文名与人类可读耗时', () => {
    render(
      <TraceTimeline
        entries={[
          { node: 'requirement_parser', status: 'ok', elapsed_ms: 320 },
          { node: 'qc_checker', status: 'ok', elapsed_ms: 1200 },
          { node: 'video_generator', status: 'ok', elapsed_ms: 88_000 },
        ]}
      />,
    );

    expect(screen.getByText('需求解析')).toBeInTheDocument();
    expect(screen.getByText('质量检查')).toBeInTheDocument();
    expect(screen.getByText('视频生成')).toBeInTheDocument();
    expect(screen.getByText('320ms')).toBeInTheDocument();
    expect(screen.getByText('1.2s')).toBeInTheDocument();
    expect(screen.getByText('1m28s')).toBeInTheDocument();
  });

  it('逐镜条目显示 1-based 序号，且不重复节点级耗时', () => {
    render(
      <TraceTimeline
        entries={[
          { node: 'video_generator#1', status: 'ok', elapsed_ms: 0 },
          { node: 'video_generator#2', status: 'ok', elapsed_ms: 0 },
          { node: 'video_generator', status: 'ok', elapsed_ms: 90_000 },
        ]}
      />,
    );

    expect(screen.getByText(/第 1 个/)).toBeInTheDocument();
    expect(screen.getByText(/第 2 个/)).toBeInTheDocument();
    // ★ 进度标记不显示耗时：0ms 会让人以为这一镜没花时间
    expect(screen.queryByText('0ms')).toBeNull();
    // 真实耗时才显示
    expect(screen.getByText('1m30s')).toBeInTheDocument();
  });

  it('失败条目可见（不能与成功长得一样）', () => {
    render(<TraceTimeline entries={[{ node: 'qc_checker', status: 'failed', elapsed_ms: 800 }]} />);

    expect(screen.getByText('✕')).toBeInTheDocument();
    expect(screen.getByText('质量检查')).toHaveClass('text-red-700');
  });

  it('复用条目标注「复用」而不是显示成新生成', () => {
    render(
      <TraceTimeline entries={[{ node: 'video_generator#3', status: 'reused', elapsed_ms: 0 }]} />,
    );

    expect(screen.getByText('复用')).toBeInTheDocument();
    expect(screen.getByText('↺')).toBeInTheDocument();
  });

  it('未登记的节点名回退显示原始名，不渲染成 undefined', () => {
    render(<TraceTimeline entries={[{ node: 'brand_new_node', status: 'ok', elapsed_ms: 10 }]} />);

    expect(screen.getByText('brand_new_node')).toBeInTheDocument();
    expect(screen.queryByText(/undefined/)).toBeNull();
  });

  it('★ 同一节点多次执行要能区分轮次（自愈循环下 QC 会跑好几遍）', () => {
    render(
      <TraceTimeline
        entries={[
          { node: 'qc_checker', status: 'ok', elapsed_ms: 1200 },
          { node: 'fix_looping', status: 'ok', elapsed_ms: 90_000 },
          { node: 'qc_checker@2', status: 'ok', elapsed_ms: 1100 },
          { node: 'qc_checker@3', status: 'failed', elapsed_ms: 1000 },
        ]}
      />,
    );

    expect(screen.getByText(/第 2 次/)).toBeInTheDocument();
    expect(screen.getByText(/第 3 次/)).toBeInTheDocument();
    // 四行的可见文本必须互不相同 —— 否则时间线上就是几行一模一样的「质量检查」
    const rows = screen.getAllByRole('listitem').map((li) => li.textContent);
    expect(new Set(rows).size).toBe(rows.length);
  });
});
