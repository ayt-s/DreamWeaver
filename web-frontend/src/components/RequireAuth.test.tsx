/**
 * 路由守卫（阶段 2）。
 *
 * 注意这只是体验层：真正的边界在后端 `SecurityConfig`。所以这里只锁两件事 ——
 * 没 token 时**不渲染受保护页面**（避免闪一下空数据），有 token 时正常渲染。
 */
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import RequireAuth from './RequireAuth';
import { getToken } from '../api/auth';

vi.mock('../api/auth', () => ({ getToken: vi.fn() }));

const mockedGetToken = vi.mocked(getToken);

function renderGuarded() {
  render(
    <MemoryRouter initialEntries={['/']}>
      <Routes>
        <Route path="/login" element={<p>登录页</p>} />
        <Route
          path="/"
          element={
            <RequireAuth>
              <p>受保护的创作页</p>
            </RequireAuth>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe('RequireAuth', () => {
  beforeEach(() => vi.clearAllMocks());

  it('没有 token → 跳到登录页，且不渲染受保护内容', () => {
    mockedGetToken.mockReturnValue(null);
    renderGuarded();
    expect(screen.getByText('登录页')).toBeTruthy();
    expect(screen.queryByText('受保护的创作页')).toBeNull();
  });

  it('有 token → 正常渲染', () => {
    mockedGetToken.mockReturnValue('jwt-token');
    renderGuarded();
    expect(screen.getByText('受保护的创作页')).toBeTruthy();
  });
});
