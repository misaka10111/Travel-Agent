import { useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { api } from '../api/client';
import type { TripCreatePayload, TripInfo } from '../api/types';

const EMPTY_TRIP: TripInfo = {
  destination: '',
  origin: '',
  start_date: '',
  end_date: '',
  travelers: '',
  companions: [],
  budget_tiers: [],
  total_budget: '',
  purposes: [],
  special_needs: [],
};

const TRAVELERS = ['1 人', '2 人', '3 人', '4 人', '5 人', '6 人及以上'];
const BUDGET_TIERS = ['经济', '舒适', '豪华', '不设限'];
const PURPOSES = [
  '休闲度假', '观光打卡', '美食之旅', '文化历史',
  '亲子游', '蜜月', '商务出行', '户外探险', '购物',
];

type MultiKey = 'budget_tiers' | 'purposes';
type SingleKey =
  | 'destination'
  | 'origin'
  | 'start_date'
  | 'end_date'
  | 'travelers'
  | 'total_budget';

function TripField({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="profile-field">
      <div className="profile-field-label">{label}</div>
      {children}
    </div>
  );
}

function computeDays(start: string, end: string): number {
  if (!start || !end) return 0;
  const s = new Date(start).getTime();
  const e = new Date(end).getTime();
  if (Number.isNaN(s) || Number.isNaN(e) || e < s) return 0;
  return Math.round((e - s) / 86400000) + 1;
}

function toTripPayload(form: TripInfo): TripCreatePayload {
  const days = computeDays(form.start_date, form.end_date);
  const title = form.destination
    ? days > 0
      ? `${form.destination}${days}日游`
      : `${form.destination}之旅`
    : '未命名行程';
  return {
    title,
    destination: form.destination,
    origin: form.origin,
    start_date: form.start_date,
    end_date: form.end_date,
    travelers: form.travelers,
    budget: form.total_budget ? Number(form.total_budget) : null,
    budget_tiers: form.budget_tiers,
    purposes: form.purposes,
  };
}

export function TripSurveyPage() {
  const [form, setForm] = useState<TripInfo>(() => {
    try {
      const raw = localStorage.getItem('tripInfo');
      return raw ? { ...EMPTY_TRIP, ...(JSON.parse(raw) as Partial<TripInfo>) } : EMPTY_TRIP;
    } catch {
      return EMPTY_TRIP;
    }
  });
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');

  function setSingle(key: SingleKey, value: string) {
    setSaved(false);
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function toggleMulti(key: MultiKey, value: string) {
    setSaved(false);
    setForm((prev) => {
      const current = prev[key];
      return {
        ...prev,
        [key]: current.includes(value)
          ? current.filter((v) => v !== value)
          : [...current, value],
      };
    });
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    localStorage.setItem('tripInfo', JSON.stringify(form));
    setSaved(false);
    setError('');

    try {
      await api.createTrip(toTripPayload(form));
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="agent-page">
      <header className="agent-hero">
        <span className="agent-kicker">本次出行</span>
        <h1>
          告诉我们 <span>这次去哪</span>
        </h1>
        <p>填好基本信息，让我们为你规划行程、订酒店和机票。</p>
      </header>

      <section className="agent-search-card profile-card">
        <form onSubmit={handleSubmit}>
          <div className="agent-section-heading-row">
            <div>
              <span className="agent-section-label">旅行信息</span>
              <h2>关于这次旅行</h2>
            </div>
            <span className="agent-status-badge ready">7 项信息</span>
          </div>

          <TripField label="1. 目的地">
            <input
              className="login-input"
              required
              value={form.destination}
              onChange={(e) => setSingle('destination', e.target.value)}
              placeholder="例如：杭州"
            />
          </TripField>

          <TripField label="2. 出发地">
            <input
              className="login-input"
              value={form.origin}
              onChange={(e) => setSingle('origin', e.target.value)}
              placeholder="例如：北京"
            />
          </TripField>

          <TripField label="3. 出行日期">
            <div className="trip-date-row">
              <input
                className="login-input"
                type="date"
                value={form.start_date}
                onChange={(e) => setSingle('start_date', e.target.value)}
                aria-label="出发日期"
              />
              <span className="trip-date-arrow">→</span>
              <input
                className="login-input"
                type="date"
                value={form.end_date}
                onChange={(e) => setSingle('end_date', e.target.value)}
                aria-label="返程日期"
              />
            </div>
          </TripField>

          <TripField label="4. 出行人数">
            <div className="profile-options">
              {TRAVELERS.map((t) => (
                <button
                  key={t}
                  type="button"
                  className={`profile-option${form.travelers === t ? ' active' : ''}`}
                  onClick={() => setSingle('travelers', t)}
                >
                  {t}
                </button>
              ))}
            </div>
          </TripField>

          <TripField label="5. 预算档位（可多选）">
            <div className="profile-options">
              {BUDGET_TIERS.map((o) => (
                <button
                  key={o}
                  type="button"
                  className={`profile-option${form.budget_tiers.includes(o) ? ' active' : ''}`}
                  onClick={() => toggleMulti('budget_tiers', o)}
                >
                  {o}
                </button>
              ))}
            </div>
          </TripField>

          <TripField label="6. 总预算金额（可选）">
            <input
              className="login-input"
              type="number"
              min="0"
              value={form.total_budget}
              onChange={(e) => setSingle('total_budget', e.target.value)}
              placeholder="单位：元，例如 5000"
            />
          </TripField>

          <TripField label="7. 旅行目的（可多选）">
            <div className="profile-options">
              {PURPOSES.map((o) => (
                <button
                  key={o}
                  type="button"
                  className={`profile-option${form.purposes.includes(o) ? ' active' : ''}`}
                  onClick={() => toggleMulti('purposes', o)}
                >
                  {o}
                </button>
              ))}
            </div>
          </TripField>

          <div className="profile-actions">
            <button type="submit" className="agent-primary-button">
              保存
            </button>
            {saved && <span className="agent-status-badge complete">已保存 ✓</span>}
            {error && <span className="login-error">{error}</span>}
          </div>
        </form>
      </section>
    </div>
  );
}
