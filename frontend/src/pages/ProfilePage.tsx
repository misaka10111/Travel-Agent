import { useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../api/client';
import type { UserProfile } from '../api/types';

const EMPTY_PROFILE: UserProfile = {
  age_group: '',
  gender: '',
  identity: '',
  city: '',
  travel_style: [],
};

const AGE_GROUPS = ['18 岁以下', '18-25', '26-35', '36-45', '46-60', '60 岁以上'];
const GENDERS = ['男', '女', '不便透露'];
const IDENTITIES = ['学生', '上班族', '自由职业', '创业者', '退休', '其他'];
const TRAVEL_STYLES = [
  '休闲度假', '深度文化', '自然风光', '美食探店',
  '亲子乐园', '购物血拼', '冒险户外', '摄影旅拍',
];

function ProfileField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="profile-field">
      <div className="profile-field-label">{label}</div>
      {children}
    </div>
  );
}

export function ProfilePage() {
  const navigate = useNavigate();
  const [form, setForm] = useState<UserProfile>(() => {
    try {
      const raw = localStorage.getItem('userProfile');
      return raw
        ? { ...EMPTY_PROFILE, ...(JSON.parse(raw) as Partial<UserProfile>) }
        : EMPTY_PROFILE;
    } catch {
      return EMPTY_PROFILE;
    }
  });
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');

  function setSingle(key: 'age_group' | 'gender' | 'identity' | 'city', value: string) {
    setSaved(false);
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function toggleStyle(value: string) {
    setSaved(false);
    setForm((prev) => ({
      ...prev,
      travel_style: prev.travel_style.includes(value)
        ? prev.travel_style.filter((v) => v !== value)
        : [...prev.travel_style, value],
    }));
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    localStorage.setItem('userProfile', JSON.stringify(form));
    setSaved(false);
    setError('');

    const userId = localStorage.getItem('currentUser');
    if (!userId) {
      navigate('/trip-survey');
      return;
    }
    try {
      await api.saveProfile(userId, form);
      setSaved(true);
      navigate('/trip-survey');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="agent-page">
      <header className="agent-hero">
        <span className="agent-kicker">你的画像</span>
        <h1>
          告诉我们 <span>你是谁</span>
        </h1>
        <p>回答 5 个小问题，让我们更懂你的旅行偏好。</p>
      </header>

      <section className="agent-search-card profile-card">
        <form onSubmit={handleSubmit}>
          <div className="agent-section-heading-row">
            <div>
              <span className="agent-section-label">用户画像</span>
              <h2>关于你</h2>
            </div>
            <span className="agent-status-badge ready">5 个问题</span>
          </div>

          <ProfileField label="1. 你的年龄">
            <div className="profile-options">
              {AGE_GROUPS.map((o) => (
                <button
                  key={o}
                  type="button"
                  className={`profile-option${form.age_group === o ? ' active' : ''}`}
                  onClick={() => setSingle('age_group', o)}
                >
                  {o}
                </button>
              ))}
            </div>
          </ProfileField>

          <ProfileField label="2. 你的性别">
            <div className="profile-options">
              {GENDERS.map((o) => (
                <button
                  key={o}
                  type="button"
                  className={`profile-option${form.gender === o ? ' active' : ''}`}
                  onClick={() => setSingle('gender', o)}
                >
                  {o}
                </button>
              ))}
            </div>
          </ProfileField>

          <ProfileField label="3. 你的身份">
            <div className="profile-options">
              {IDENTITIES.map((o) => (
                <button
                  key={o}
                  type="button"
                  className={`profile-option${form.identity === o ? ' active' : ''}`}
                  onClick={() => setSingle('identity', o)}
                >
                  {o}
                </button>
              ))}
            </div>
          </ProfileField>

          <ProfileField label="4. 常驻城市">
            <input
              className="login-input"
              value={form.city}
              onChange={(e) => setSingle('city', e.target.value)}
              placeholder="例如：上海"
            />
          </ProfileField>

          <ProfileField label="5. 旅行风格（可多选）">
            <div className="profile-options">
              {TRAVEL_STYLES.map((o) => (
                <button
                  key={o}
                  type="button"
                  className={`profile-option${form.travel_style.includes(o) ? ' active' : ''}`}
                  onClick={() => toggleStyle(o)}
                >
                  {o}
                </button>
              ))}
            </div>
          </ProfileField>

          <div className="profile-actions">
            <button type="submit" className="agent-primary-button">
              保存画像
            </button>
            {saved && <span className="agent-status-badge complete">已保存 ✓</span>}
            {error && <span className="login-error">{error}</span>}
          </div>
        </form>
      </section>
    </div>
  );
}
