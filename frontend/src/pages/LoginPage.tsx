import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';

const PHONE_RE = /^1[3-9]\d{9}$/;

export function LoginPage() {
  const [phone, setPhone] = useState('');
  const [code, setCode] = useState('');
  const [sentCode, setSentCode] = useState('');
  const [countdown, setCountdown] = useState(0);
  const [error, setError] = useState('');
  const navigate = useNavigate();

  useEffect(() => {
    if (countdown <= 0) return;
    const timer = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(timer);
  }, [countdown]);

  async function sendCode() {
    if (!PHONE_RE.test(phone)) {
      setError('请输入正确的手机号');
      return;
    }
    setError('');
    try {
      const res = await api.sendCode(phone);
      setSentCode(res.code);
      setCountdown(60);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!sentCode) {
      setError('请先获取验证码');
      return;
    }
    setError('');
    try {
      const res = await api.login(phone, code);
      localStorage.setItem('currentUser', res.phone);
      navigate(res.is_new ? '/profile' : '/agent');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="agent-page">
      <header className="agent-hero">
        <span className="agent-kicker">Welcome Back</span>
        <h1>
          你好，欢迎回到 <span>TravelAgent</span>
        </h1>
        <p>使用手机号验证码登录，继续规划你的下一段旅程。</p>
      </header>

      <section className="agent-search-card login-card">
        <form onSubmit={handleSubmit}>
          <div className="agent-section-heading-row">
            <div>
              <span className="agent-section-label">登录</span>
              <h2>手机号验证码登录</h2>
            </div>
            <span className="agent-status-badge ready">快速登录</span>
          </div>

          <div className="login-field">
            <label htmlFor="phone">手机号</label>
            <input
              id="phone"
              className="login-input"
              inputMode="numeric"
              maxLength={11}
              value={phone}
              onChange={(e) => setPhone(e.target.value.replace(/\D/g, ''))}
              placeholder="请输入 11 位手机号"
            />
          </div>

          <div className="login-field">
            <label htmlFor="code">验证码</label>
            <div className="login-code-row">
              <input
                id="code"
                className="login-input"
                inputMode="numeric"
                maxLength={6}
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
                placeholder="请输入验证码"
              />
              <button
                type="button"
                className="login-send-btn"
                disabled={countdown > 0}
                onClick={sendCode}
              >
                {countdown > 0 ? `${countdown}s 后重发` : '发送验证码'}
              </button>
            </div>
          </div>

          {sentCode && <div className="login-hint">开发模式验证码：{sentCode}</div>}
          {error && <div className="login-error">{error}</div>}

          <button type="submit" className="agent-primary-button login-submit">
            登录
          </button>
        </form>
      </section>
    </div>
  );
}
