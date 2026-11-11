import axios from 'axios';
import type { CommonResult } from '../types/task';
import { clearSession, getToken } from './session';

// 统一 axios 实例：dev 走 vite 代理到 Java 8080
const client = axios.create({
  baseURL: '/api',
  timeout: 30_000,
});

/**
 * 请求头：带 JWT。
 *
 * ★ 2026-11-10：此前这里发的是 `X-User-Id`（值可任意伪造，等于没有鉴权）。
 * 现在身份只由签名过的 token 决定，服务端 `UserContext` 也从 SecurityContext 取值。
 */
client.interceptors.request.use((config) => {
  const token = getToken();
  if (token) {
    config.headers.set('Authorization', `Bearer ${token}`);
  }
  return config;
});

/**
 * 401：登录态失效 → 清会话并回登录页。
 *
 * 登录/注册接口本身排除在外，否则密码输错会触发跳转死循环。
 */
client.interceptors.response.use(
  (resp) => resp,
  (error) => {
    const status = error?.response?.status;
    const url: string = error?.config?.url ?? '';
    if (status === 401 && !url.includes('/auth/')) {
      clearSession();
      if (window.location.pathname !== '/login') {
        window.location.href = '/login';
      }
    }
    return Promise.reject(error);
  },
);



// 统一解包 CommonResult：code=0 返回 data，否则抛错
export async function unwrap<T>(promise: Promise<{ data: CommonResult<T> }>): Promise<T> {
  const resp = await promise;
  if (resp.data.code !== 0) {
    throw new Error(resp.data.message || '请求失败');
  }
  return resp.data.data as T;
}

export default client;