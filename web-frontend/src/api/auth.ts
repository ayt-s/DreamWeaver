/**
 * 账号会话（阶段 2，2026-11-10）。
 *
 * 为什么放在 localStorage 而不是 Cookie：后端是无状态 JWT（`SecurityConfig` 里
 * `SessionCreationPolicy.STATELESS` + 关闭 CSRF），token 由 `Authorization: Bearer` 携带。
 * 若将来改成 Cookie 会话，**必须同时打开 CSRF**，否则就是另一种漏洞。
 */
import client, { unwrap } from './client';
import { clearSession, getToken, saveSession, type AuthUser } from './session';

// 会话读写的唯一实现在 session.ts（client 也要读 token，放这里会循环依赖）
export { currentUser, getToken, clearSession, type AuthUser } from './session';

interface AuthResponse {
  token: string;
  userId: number;
  username: string;
}

async function authCall(path: string, username: string, password: string): Promise<AuthUser> {
  const resp = await unwrap<AuthResponse>(client.post(path, { username, password }));
  const user = { userId: resp.userId, username: resp.username };
  saveSession(resp.token, user);
  return user;
}

export function login(username: string, password: string): Promise<AuthUser> {
  return authCall('/auth/login', username, password);
}

export function register(username: string, password: string): Promise<AuthUser> {
  return authCall('/auth/register', username, password);
}

export async function fetchMe(): Promise<AuthUser> {
  const data = await unwrap<AuthUser>(client.get('/auth/me'));
  const user = { userId: data.userId, username: data.username };
  saveSession(getToken() ?? '', user);
  return user;
}

export function logout(): void {
  clearSession();
}
