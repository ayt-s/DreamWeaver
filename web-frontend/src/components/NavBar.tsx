import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import { BookOpen, Film, LayoutGrid, LogOut, Sparkles, Wand2 } from 'lucide-react';

import { currentUser, logout } from '../api/auth';

/**
 * 全局顶部导航：四个页面一键切换，不依赖 CreatePage 的按钮。
 * 阶段 2 起右侧显示当前账号 + 退出；登录页不显示页面导航（避免"未登录也能点进去"的错觉）。
 */
export default function NavBar() {
  const location = useLocation();
  const navigate = useNavigate();
  const user = currentUser();
  const onLoginPage = location.pathname === '/login';

  const navItems = [
    { to: '/', label: '创作', icon: Sparkles, end: true },
    { to: '/gallery', label: '画廊', icon: LayoutGrid },
    { to: '/novel', label: '小说转漫剧', icon: BookOpen },
    { to: '/canvas', label: '无限画布', icon: Wand2 },
  ];

  return (
    <nav className="sticky top-0 z-30 border-b border-slate-100 bg-white/80 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center gap-1 px-4 py-2">
        <div className="mr-4 flex items-center gap-1.5">
          <Film className="h-4 w-4 text-violet-600" />
          <span className="text-sm font-bold text-slate-800">DreamWeaver</span>
        </div>
        {!onLoginPage &&
          navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-colors ${
                  isActive
                    ? 'bg-violet-100 text-violet-700'
                    : 'text-slate-500 hover:bg-slate-100 hover:text-slate-700'
                }`
              }
            >
              <item.icon className="h-3.5 w-3.5" />
              {item.label}
            </NavLink>
          ))}

        {user && !onLoginPage && (
          <div className="ml-auto flex items-center gap-2">
            <span className="text-[11px] text-slate-500" title={`用户 id ${user.userId}`}>
              {user.username}
            </span>
            <button
              type="button"
              onClick={() => {
                logout();
                navigate('/login', { replace: true });
              }}
              className="inline-flex items-center gap-1 rounded-lg px-2 py-1 text-[11px] text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-700"
            >
              <LogOut className="h-3 w-3" />
              退出
            </button>
          </div>
        )}
      </div>
    </nav>
  );
}
