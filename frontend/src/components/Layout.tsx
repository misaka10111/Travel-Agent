import { useEffect, useState } from 'react';
import { Link, Outlet, useLocation, useNavigate } from 'react-router-dom';

function maskPhone(phone: string): string {
  return phone.length === 11 ? `${phone.slice(0, 3)}****${phone.slice(7)}` : phone;
}

export function Layout() {
  const location = useLocation();
  const navigate = useNavigate();
  const [user, setUser] = useState(() => localStorage.getItem('currentUser') || '');

  useEffect(() => {
    setUser(localStorage.getItem('currentUser') || '');
  }, [location.pathname]);

  function logout() {
    localStorage.removeItem('currentUser');
    setUser('');
    navigate('/login');
  }

  return (
    <div className="app-shell">
      <header className="app-header">
        <Link to="/" className="brand">
          TravelAgent
        </Link>
        <nav>
          <Link to="/">目的地</Link>
          <Link to="/trips">我的行程</Link>
          <Link to="/agent">AI 助手</Link>
          <Link to="/profile">用户画像</Link>
          <Link to="/trip-survey">本次旅行</Link>
          {user ? (
            <>
              <span className="nav-user-phone">{maskPhone(user)}</span>
              <button type="button" className="nav-logout" onClick={logout}>
                退出
              </button>
            </>
          ) : (
            <Link to="/login">登录</Link>
          )}
          <Link to="/profile" className="nav-user" aria-label="个人用户">
            <svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="12" cy="8" r="4" />
              <path d="M4 21c0-4 3.6-6 8-6s8 2 8 6" />
            </svg>
          </Link>
        </nav>
      </header>
      <main className="app-main">
        <Outlet />
      </main>
    </div>
  );
}
