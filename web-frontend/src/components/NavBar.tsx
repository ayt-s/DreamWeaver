import { NavLink } from 'react-router-dom';
import { BookOpen, Film, LayoutGrid, Sparkles, Wand2 } from 'lucide-react';

/**
 * 全局顶部导航：四个页面一键切换，不依赖 CreatePage 的按钮。
 */
export default function NavBar() {
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
        {navItems.map((item) => (
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
      </div>
    </nav>
  );
}
