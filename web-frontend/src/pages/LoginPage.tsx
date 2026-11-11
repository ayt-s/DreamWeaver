/**
 * 登录 / 注册页（阶段 2）。
 *
 * 设计取舍：不引入表单库，两个受控输入 + 一个提交按钮 —— 这个页面的复杂度不值得。
 * 错误提示统一从后端 message 取（后端对「用户不存在」与「密码错」给同一句话，
 * 前端也就不该自己编差异文案）。
 */
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Film, Loader2 } from 'lucide-react';

import { getToken, login, register } from '../api/auth';

type Mode = 'login' | 'register';

export default function LoginPage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  // 已登录直接进创作页，避免"回退键回到登录页"的怪异状态
  useEffect(() => {
    if (getToken()) {
      navigate('/', { replace: true });
    }
  }, [navigate]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setBusy(true);
    try {
      if (mode === 'login') {
        await login(username, password);
      } else {
        await register(username, password);
      }
      navigate('/', { replace: true });
    } catch (err) {
      const msg = err instanceof Error ? err.message : '请求失败，请稍后重试';
      setError(msg);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-[calc(100vh-49px)] items-center justify-center bg-slate-50 px-4">
      <form
        onSubmit={submit}
        className="w-full max-w-sm rounded-2xl border border-slate-200 bg-white p-6 shadow-sm"
      >
        <div className="mb-5 flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-violet-100">
            <Film className="h-4 w-4 text-violet-600" />
          </div>
          <div>
            <h1 className="text-sm font-bold text-slate-900">DreamWeaver</h1>
            <p className="text-[11px] text-slate-500">
              {mode === 'login' ? '登录后继续创作' : '注册一个新账号'}
            </p>
          </div>
        </div>

        <label className="mb-1 block text-[11px] font-medium text-slate-500">用户名</label>
        <input
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          placeholder="3–32 位，字母/数字/下划线/中文"
          className="mb-3 w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-800 outline-none focus:border-violet-400 focus:bg-white"
        />

        <label className="mb-1 block text-[11px] font-medium text-slate-500">密码</label>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
          placeholder={mode === 'login' ? '输入密码' : '至少 8 位'}
          className="mb-3 w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-800 outline-none focus:border-violet-400 focus:bg-white"
        />

        {error && (
          <p role="alert" className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-[11px] text-red-600">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={busy || !username.trim() || !password}
          className="inline-flex w-full items-center justify-center gap-1.5 rounded-lg bg-violet-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-violet-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {mode === 'login' ? '登录' : '注册并登录'}
        </button>

        <button
          type="button"
          onClick={() => {
            setMode(mode === 'login' ? 'register' : 'login');
            setError('');
          }}
          className="mt-3 w-full text-center text-[11px] text-slate-500 hover:text-violet-600"
        >
          {mode === 'login' ? '没有账号？注册一个' : '已有账号？去登录'}
        </button>
      </form>
    </div>
  );
}
