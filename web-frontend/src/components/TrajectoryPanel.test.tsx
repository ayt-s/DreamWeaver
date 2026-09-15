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

import { QcReportBlock } from './TrajectoryPanel';
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
