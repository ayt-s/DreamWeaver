/**
 * 登录页（阶段 2）的交互契约。
 *
 * 锁住三件事：
 * 1. 提交把用户输入原样交给 `login`（不要自己 trim 掉中文/空格语义之外的差异）
 * 2. 后端错误文案**原样展示**（后端对「用户不存在」和「密码错」给同一句，
 *    前端不能再编一个更具体的，否则等于帮撞库做探针）
 * 3. 注册模式调的是 `register` 而不是 `login`
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import LoginPage from './LoginPage';
import { getToken, login, register } from '../api/auth';

vi.mock('../api/auth', () => ({
  login: vi.fn(),
  register: vi.fn(),
  getToken: vi.fn().mockReturnValue(null),
}));

const mockedLogin = vi.mocked(login);
const mockedRegister = vi.mocked(register);
const mockedGetToken = vi.mocked(getToken);

function renderPage() {
  render(
    <MemoryRouter initialEntries={['/login']}>
      <LoginPage />
    </MemoryRouter>,
  );
}

describe('LoginPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockedGetToken.mockReturnValue(null);
  });

  it('提交用户名密码 → 调用 login', async () => {
    mockedLogin.mockResolvedValue({ userId: 1, username: 'admin' });
    renderPage();

    fireEvent.change(screen.getByPlaceholderText(/3–32 位/), { target: { value: 'admin' } });
    fireEvent.change(screen.getByPlaceholderText('输入密码'), { target: { value: 'secret123' } });
    fireEvent.click(screen.getByRole('button', { name: '登录' }));

    await waitFor(() => expect(mockedLogin).toHaveBeenCalledWith('admin', 'secret123'));
  });

  it('登录失败 → 原样显示后端的文案', async () => {
    mockedLogin.mockRejectedValue(new Error('用户名或密码错误'));
    renderPage();

    fireEvent.change(screen.getByPlaceholderText(/3–32 位/), { target: { value: 'admin' } });
    fireEvent.change(screen.getByPlaceholderText('输入密码'), { target: { value: 'bad' } });
    fireEvent.click(screen.getByRole('button', { name: '登录' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('用户名或密码错误');
  });

  it('切换到注册模式后调用 register', async () => {
    mockedRegister.mockResolvedValue({ userId: 2, username: 'newbie' });
    renderPage();

    fireEvent.click(screen.getByRole('button', { name: /注册一个/ }));
    fireEvent.change(screen.getByPlaceholderText(/3–32 位/), { target: { value: 'newbie' } });
    fireEvent.change(screen.getByPlaceholderText('至少 8 位'), { target: { value: 'pass1234' } });
    fireEvent.click(screen.getByRole('button', { name: '注册并登录' }));

    await waitFor(() => expect(mockedRegister).toHaveBeenCalledWith('newbie', 'pass1234'));
    expect(mockedLogin).not.toHaveBeenCalled();
  });
});
