import { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { AgentPage } from '../pages/AgentPage';

const navItems = [
  { to: '/', label: '首页', end: true },
  { to: '/agent', label: 'AI 助手' },
  { to: '/trips', label: '历史行程' },
];

export function Layout() {
  const navigate = useNavigate();
  const location = useLocation();
  const [currentUser, setCurrentUser] = useState(
    () => localStorage.getItem('currentUser') || '',
  );

  const isAgentPage = location.pathname === '/agent' || location.pathname.startsWith('/agent/');

  useEffect(() => {
    setCurrentUser(localStorage.getItem('currentUser') || '');
  }, [location.pathname]);

  useEffect(() => {
    const update = () => setCurrentUser(localStorage.getItem('currentUser') || '');
    window.addEventListener('storage', update);
    return () => window.removeEventListener('storage', update);
  }, []);

  function logout() {
    localStorage.removeItem('currentUser');
    setCurrentUser('');
    navigate('/login');
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <NavLink to="/" className="brand" aria-label="TravelAgent 首页">
          TravelAgent
        </NavLink>

        <nav className="app-nav" aria-label="主导航">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                `app-nav-link ${isActive ? 'active' : ''}`
              }
            >
              {item.label}
            </NavLink>
          ))}

          {currentUser ? (
            <span className="app-login-status">
              <span className="app-login-phone">已登录 · {currentUser}</span>
              <button type="button" className="app-logout-button" onClick={logout}>
                退出
              </button>
            </span>
          ) : (
            <NavLink
              to="/login"
              className={({ isActive }) =>
                `app-nav-link ${isActive ? 'active' : ''}`
              }
            >
              登录
            </NavLink>
          )}

          <NavLink
            to="/profile"
            className={({ isActive }) =>
              `app-profile-button ${isActive ? 'active' : ''}`
            }
            aria-label="个人资料"
          >
            <span className="app-profile-icon" aria-hidden="true">
              <svg
                viewBox="0 0 24 24"
                width="20"
                height="20"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <circle cx="12" cy="8" r="4" />
                <path d="M4.8 20c.8-4 3.2-6 7.2-6s6.4 2 7.2 6" />
              </svg>
            </span>
          </NavLink>
        </nav>
      </header>

      {/*
        普通页面继续由 Outlet 渲染。
        切到 AI 助手时将普通页面卸载，但 AgentPage 本身始终保持挂载，
        只通过 display 控制显隐，因此聊天、路线、待选行程、地图位置等 state
        在页面间切换时不会被 React 清空。
      */}
      {!isAgentPage && (
        <main className="app-main">
          <Outlet />
        </main>
      )}

      <main
        className="app-main"
        style={{ display: isAgentPage ? undefined : 'none' }}
        aria-hidden={!isAgentPage}
      >
        <AgentPage />
      </main>
    </div>
  );
}

export default Layout;
