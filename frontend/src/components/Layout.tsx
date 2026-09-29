import { NavLink, Outlet } from 'react-router-dom';

const navItems = [
  { to: '/', label: '首页', end: true },
  { to: '/agent', label: 'AI 助手' },
  { to: '/trips', label: '历史行程' },
  { to: '/profile', label: '用户画像' },
  { to: '/trip-survey', label: '本次旅行' },
  { to: '/login', label: '登录' },
];

export function Layout() {
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

      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}

export default Layout;
