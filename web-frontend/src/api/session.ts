/**
 * 会话存储（纯 localStorage，不依赖 axios）。
 *
 * 单独成文件是为了**避免循环依赖**：`client.ts` 需要读 token 加请求头，
 * `auth.ts` 需要 client 发请求 —— 两边都只依赖这里。
 */

const TOKEN_KEY = 'dreamweaver:token';
const USER_KEY = 'dreamweaver:user';

export interface AuthUser {
  userId: number;
  username: string;
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function currentUser(): AuthUser | null {
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as AuthUser) : null;
  } catch {
    return null;
  }
}

export function saveSession(token: string, user: AuthUser): void {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  // 阶段 1 的身份头来源，登录体系上线后不再使用
  localStorage.removeItem('dreamweaver:user-id');
}
