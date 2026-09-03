import axios from 'axios';
import type { CommonResult } from '../types/task';

// 统一 axios 实例：dev 走 vite 代理到 Java 8080
const client = axios.create({
  baseURL: '/api',
  timeout: 30_000,
});

// 统一解包 CommonResult：code=0 返回 data，否则抛错
export async function unwrap<T>(promise: Promise<{ data: CommonResult<T> }>): Promise<T> {
  const resp = await promise;
  if (resp.data.code !== 0) {
    throw new Error(resp.data.message || '请求失败');
  }
  return resp.data.data as T;
}

export default client;