/**
 * 路由守卫（阶段 2）：没有 token 一律回登录页。
 *
 * 这只是**体验层**的拦截，不是安全边界 —— 真正的访问控制在后端
 * （`SecurityConfig` 的 `anyRequest().authenticated()`）。前端拦截的目的是
 * 避免"页面先闪一下空数据再跳登录"。
 */
import type { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';

import { getToken } from '../api/auth';

export default function RequireAuth({ children }: { children: ReactNode }) {
  const location = useLocation();
  if (!getToken()) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }
  return <>{children}</>;
}
