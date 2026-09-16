import axios from 'axios';
import type { CommonResult } from '../types/task';

// 统一 axios 实例：dev 走 vite 代理到 Java 8080
const client = axios.create({
  baseURL: '/api',
  timeout: 30_000,
});

/** 身份头：后端 UserContext 读它（缺失回落 1）。

 * ⚠️ 现在**不是鉴权**——值可以随便伪造，真正的访问控制要等接了登录/JWT。

 * 阶段 1 的意义只是「把散落的用户常量收到一处」：以后接登录时改这里 + 后端 UserContext。 */

const USER_ID_KEY = 'dreamweaver:user-id';

client.interceptors.request.use((config) => {

  const uid = localStorage.getItem(USER_ID_KEY) || '1';

  config.headers.set('X-User-Id', uid);

  return config;

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