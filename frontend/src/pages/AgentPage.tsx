import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { useLocation } from 'react-router-dom';
import { planning } from '../api/planning';
import type { SessionCredential, SessionView } from '../api/planning';

type Message = { id: string; role: 'user' | 'assistant'; text: string };
const SAVED = 'travelPlanningSession';
const STATUS: Record<string, string> = { draft: '待规划', running: '规划中', waiting_user: '等待回答', needs_attention: '需要处理', completed: '草案已交付', cancelled: '已取消', failed: '失败' };
const CATEGORIES: Record<string, string> = { attraction: '景点', restaurant: '餐饮', hotel: '住宿', transport: '交通' };
const MODES: Record<string, string> = { walking: '步行', transit: '公共交通', driving: '驾车' };
const ATTENTION: Record<string, string> = {
  no_progress_repeated_action: '候选检索没有新增地点，系统已停止重复搜索。',
  search_stalled_no_new_candidates: '地点检索仍无新增结果，请调整条件或改用景点附近搜索。',
  budget_exhausted_or_obsolete: '本次调用额度已用完。',
  model_request_failed: '模型服务本次请求失败，请稍后重试。',
  plan_paused: '规划暂时停下，请查看具体缺口。',
  server_shutdown_resume_available: '后端重启中断了执行。地点和路线需要重新获取，可继续规划或保留需求建立新尝试。',
};
function clock(value: number | null) { return value === null ? '待确认' : `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`; }
function saved<T>(key: string, fallback: T): T { try { return JSON.parse(localStorage.getItem(key) ?? 'null') ?? fallback; } catch { return fallback; } }

export function AgentPage() {
  const location = useLocation();
  const [draft, setDraft] = useState('');
  const [view, setView] = useState<SessionView | null>(null);
  const [credential, setCredential] = useState<SessionCredential | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const initialized = useRef(false);
  const observed = useRef('');
  const state = view?.state;
  const running = state?.status === 'running';
  const lowBudget = !!view && (view.usage.model_limit - view.usage.model_calls < 3 ||
    view.usage.step_limit - view.usage.steps < 3 ||
    (state?.candidates_need_refresh && view.usage.map_limit - view.usage.map_calls < 10));
  const canRetry = state?.status === 'needs_attention' && state.retry_generation === 0 && !state.retry_child_ref;
  const plan = state?.current_plan;
  const places = new Map(state?.candidates.map(p => [p.place_id, p]) ?? []);
  const name = (id: string) => places.get(id)?.name ?? `地点待刷新 (${id.slice(0, 8)})`;
  const say = (text: string, role: Message['role'] = 'assistant') => setMessages(m => [...m, { id: crypto.randomUUID(), role, text }]);

  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    let remembered: SessionCredential | null = null;
    try { remembered = JSON.parse(sessionStorage.getItem(SAVED) ?? 'null'); } catch { remembered = null; }
    if (remembered) {
      setCredential(remembered);
      planning.get(remembered).then(setView).catch(e => { setError(String(e)); sessionStorage.removeItem(SAVED); setCredential(null); });
      return;
    }
    if ((location.state as { autostart?: boolean } | null)?.autostart) {
      const trip = saved<Record<string, unknown>>('tripInfo', {});
      const parts = [trip.origin ? `从${trip.origin}出发` : '', trip.destination ? `去${trip.destination}` : '',
        trip.start_date ? `${trip.start_date}${trip.end_date ? `至${trip.end_date}` : ''}` : '',
        trip.travelers, trip.total_budget ? `总预算${trip.total_budget}元人民币` : '',
        ...(Array.isArray(trip.purposes) ? trip.purposes : []), ...(Array.isArray(trip.special_needs) ? trip.special_needs : [])].filter(Boolean);
      setDraft(parts.join('，'));
    }
  }, [location.state]);

  useEffect(() => {
    if (!credential || !running) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const next = await planning.get(credential);
        if (!active) return;
        setView(next);
        if (next.state.status === 'running') timer = setTimeout(poll, 1500);
      } catch (e) { if (active) { setError(String(e)); timer = setTimeout(poll, 4000); } }
    };
    timer = setTimeout(poll, 1000);
    return () => { active = false; clearTimeout(timer); };
  }, [credential, running]);

  useEffect(() => {
    if (!state || state.status === 'running' || state.status === 'draft') return;
    const key = `${state.session_id}:${state.state_version}`;
    if (observed.current === key) return;
    observed.current = key;
    const question = state.pending_questions[0];
    if (question) say(question.prompt);
    else if (state.status === 'completed') say(`已生成${state.current_plan?.days.length ?? ''}天草案。请查看右侧行程和待确认项，草案不代表已经预订。`);
    else if (state.agent_message) say(state.agent_message);
    else say(`当前状态：${STATUS[state.status] ?? state.status}。${state.attention_reason ?? ''}`);
  }, [state]);

  async function submit(text: string, optionId?: string) {
    if (busy || running || !text.trim()) return;
    setBusy(true); setError(''); say(text, 'user'); setDraft('');
    try {
      let current = view;
      let access = credential;
      if (!current || !access) {
        const raw = saved<Record<string, unknown>>('userProfile', {});
        const profile = Object.fromEntries(['age_group', 'gender', 'identity', 'city', 'travel_style', 'preferences'].filter(k => raw[k] !== undefined).map(k => [k, raw[k]]));
        const created = await planning.create(text, profile);
        access = { id: created.state.session_id, token: created.access_token };
        sessionStorage.setItem(SAVED, JSON.stringify(access));
        setCredential(access); current = created;
      } else if (current.state.pending_questions[0]) {
        current = await planning.answer(access, current.state.pending_questions[0], optionId ? undefined : text, optionId);
      } else {
        current = await planning.edit(access, current.state.state_version, { message: text });
      }
      setView(current);
      const next = await planning.run(access, current.state.state_version);
      setView(next);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      if (credential) planning.get(credential).then(setView).catch(() => undefined);
    } finally { setBusy(false); }
  }

  async function resume() {
    if (!credential || !state || busy || lowBudget) return;
    setBusy(true); setError('');
    try { setView(await planning.run(credential, state.state_version)); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  async function retry() {
    if (!credential || !state || busy || !canRetry) return;
    setBusy(true); setError('');
    try {
      const created = await planning.retry(credential, state.state_version);
      const nextCredential = { id: created.state.session_id, token: created.access_token };
      sessionStorage.setItem(SAVED, JSON.stringify(nextCredential));
      setCredential(nextCredential); setView(created); observed.current = '';
      say('已保留本次需求和锁定地点，开始一个新的有界尝试；旧会话的用量记录仍保留。');
      setView(await planning.run(nextCredential, created.state.state_version));
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  }
  async function cancel() {
    if (!credential || !state) return;
    try { setView(await planning.cancel(credential, state.state_version)); }
    catch (e) { setError(String(e)); }
  }
  async function toggleLock(id: string) {
    if (!credential || !state || running || busy) return;
    const selected = state.selections.find(s => s.place_id === id && s.decision === 'lock');
    const selections = state.selections.filter(s => s.place_id !== id);
    if (!selected) selections.push({ selection_id: crypto.randomUUID(), place_id: id, decision: 'lock', evidence: { source: 'selection', source_ref: 'place-card', confirmation: 'explicit' } });
    setBusy(true); setError('');
    try { setView(await planning.edit(credential, state.state_version, { selections })); say(selected ? '已解除锁定；可重新规划。' : '已锁定该地点；点击继续规划保留它。'); }
    catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  function newTrip() {
    sessionStorage.removeItem(SAVED); setCredential(null); setView(null); setMessages([]); setError(''); observed.current = '';
  }
  function onSubmit(event: FormEvent) { event.preventDefault(); void submit(draft); }

  return <section className="agent-page">
    <div className="agent-hero"><span className="agent-kicker">Travel Agent · 地图辅助规划</span>
      <h1>把旅行想法，<span>安排成每天的行程。</span></h1><p>告诉我目的地、天数和偏好；日期预算未定也可以先做草案。</p></div>
    <div className="agent-workspace">
      <section className="agent-search-card agent-chat-panel">
        <div className="agent-search-heading"><h2>这次想怎么旅行？</h2><span className="agent-status-badge ready">{state ? STATUS[state.status] : '等待输入'}</span></div>
        <div className="agent-conversation-log agent-conversation-log-v3" aria-live="polite">
          {!messages.length && <p>例如：从上海去北京玩5天，2人，喜欢人文古建，希望舒适，饮食清淡不辣，住宿交通便利。</p>}
          {messages.map(m => <div className={`agent-message ${m.role}`} key={m.id}><div className="agent-message-bubble">{m.text}</div></div>)}
          {(running || busy) && <div className="agent-message assistant"><div className="agent-message-bubble">正在检索地点、计算路线或检查安排…</div></div>}
        </div>
        {state?.pending_questions[0]?.options.map(o => <button className="profile-option" key={o.option_id} disabled={busy} onClick={() => void submit(o.label, o.option_id)}>{o.label}</button>)}
        <form className="agent-chat-composer agent-chat-composer-v3" onSubmit={onSubmit}>
          <textarea aria-label="旅行需求或问题回答" value={draft} onChange={e => setDraft(e.target.value)} rows={3} placeholder="描述旅行需求，或修改刚才的计划" onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }} />
          <div className="agent-search-footer"><span>Ctrl / ⌘ + Enter 发送</span><button type="submit" className="agent-primary-button" disabled={busy || running || !draft.trim()}>发送</button></div>
        </form>
        <div className="planning-controls">
          {state && (['draft', 'needs_attention'].includes(state.status) || state.plan_needs_refresh) && !running && <button className="profile-option" disabled={busy || lowBudget} onClick={() => void resume()}>继续规划</button>}
          {canRetry && !running && <button className="profile-option" disabled={busy} onClick={() => void retry()}>保留需求和锁定地点，重新尝试（新会话）</button>}
          {running && <button className="profile-option" onClick={() => void cancel()}>取消执行</button>}
          {!running && <button className="profile-option" disabled={busy} onClick={newTrip}>新旅行</button>}
        </div>
        {view && <p className="planning-muted">本次地图调用 {view.usage.map_calls}/{view.usage.map_limit} · 模型及检索服务调用 {view.usage.model_calls}/{view.usage.model_limit}</p>}
        {lowBudget && !running && <p className="planning-notice">本会话剩余额度不足以完成规划与检查。可建立一次新尝试；旧会话的用量不会清零。</p>}
      </section>
      <section className="agent-search-card agent-plan-panel">
        <div className="agent-search-heading"><h2>{state ? `${state.intent_snapshot.destination.label} 的行程` : '旅行草案'}</h2>
          {plan && <span className="agent-status-badge ready">版本 {plan.version} · {plan.status === 'verified' ? '已核验' : '待确认草案'}</span>}</div>
        {error && <div className="login-error" role="alert">{error}</div>}
        {(state?.candidates_need_refresh || state?.plan_needs_refresh) && <p className="planning-notice">地点或路线需要重新查询。服务重启后会保留需求和选择，重新规划后再查看供应商数据。</p>}
        {state?.candidates_stale && <p className="planning-notice">需求已更新，候选排序和原计划需要重新评估。</p>}
        {plan ? <>
          <div className="planning-notice"><strong>待确认项</strong><p>{plan.missing_requirements.join('、') || '暂无'}</p>
            <p>酒店为地点候选，房价和余房以日期查询为准。餐馆菜单尚未确认清淡或不辣。路线以高德查询条件为准。</p></div>
          {plan.audit && <p>规则检查：{plan.audit.hard_status === 'failed' ? '存在冲突' : plan.audit.hard_status === 'unknown' ? '存在待核实信息' : '通过'} · 体验检查：{plan.audit.experience_status}</p>}
          {plan.days.map(day => <div className="agent-day" key={day.day_index}>
            <h3>第 {day.day_index} 天 · {day.date ?? '日期待定'}</h3>
            {day.stops.map((stop, index) => {
              const before = day.stops[index - 1];
              const occurrence = before ? day.stops.slice(1, index).filter((prior, offset) =>
                day.stops[offset].place_id === before.place_id && prior.place_id === stop.place_id).length : 0;
              const route = before && day.routes.filter(r => r.from_place_id === before.place_id && r.to_place_id === stop.place_id)[occurrence];
              return <div key={stop.stop_id}>
                {route && <div className="planning-route"><strong>{MODES[route.mode]} · {route.status === 'ok' ? `${Math.ceil((route.duration_seconds ?? 0) / 60)} 分钟 · ${((route.distance_meters ?? 0) / 1000).toFixed(1)} 公里` : '路线待确认'}</strong>
                  <p>{route.steps.map(s => s.line_name).filter(Boolean).join(' → ') || route.unknown_reason || '高德路线估计'}</p>
                  <small>查询条件：{route.requested_departure_at} · 来源：高德地图</small></div>}
                <div className="agent-block"><div className="agent-block-left"><span>{clock(stop.start_minute)}–{clock(stop.end_minute)}</span><span>{CATEGORIES[stop.category]}</span></div>
                  <div className="agent-block-body"><strong>{name(stop.place_id)} {stop.locked ? '· 已锁定' : ''}</strong><p>{places.get(stop.place_id)?.address}</p><p>{stop.reason}</p>
                    {!!stop.child_place_ids.length && <small>同次访问内部点位：{stop.child_place_ids.map(name).join('、')}</small>}
                    {['attraction', 'hotel'].includes(stop.category) && <button className="profile-option" disabled={busy || running} onClick={() => void toggleLock(stop.place_id)}>{state?.selections.some(s => s.place_id === stop.place_id && s.decision === 'lock') ? '解除锁定' : '锁定地点'}</button>}
                  </div></div>
              </div>;
            })}
            <details><summary>当天假设与待核实事项</summary>{day.notes.map(n => <p key={n}>{n}</p>)}{plan.audit?.issues.filter(i => i.day_index === day.day_index).map((i, j) => <p key={j}>{i.severity === 'blocking' ? '冲突：' : '待确认：'}{i.message}</p>)}</details>
          </div>)}
          {!!plan.travel_offers.length && <details><summary>供应商交通与酒店候选（未预订）</summary>{plan.travel_offers.map((o, i) => <p key={i}>{o.name ?? o.category} {o.direction} {o.segments.map(s => `${s.service_no ?? ''} ${s.origin_station ?? ''}→${s.destination_station ?? ''} ${s.departure_at ?? ''}`).join(' / ')} · 报价原文 {o.price_display ?? '未知'} {o.currency ?? '币种待确认'}，计价范围 {o.price_scope}</p>)}</details>}
        </> : state?.candidates.length ? <>
          <p>候选已检索：景点 {state.coverage.attraction ?? 0} · 餐饮 {state.coverage.restaurant ?? 0} · 酒店 {state.coverage.hotel ?? 0}。候选数量不代表完整行程已完成。</p>
          <div className="agent-recommendation-grid">{state.candidates.slice(0, 24).map(p => <div className="planning-candidate" key={p.place_id}><strong>{p.name}</strong><p>{CATEGORIES[p.category] ?? p.category} · {p.address}</p><button className="profile-option" disabled={busy || running} onClick={() => void toggleLock(p.place_id)}>{state.selections.some(s => s.place_id === p.place_id && s.decision === 'lock') ? '解除锁定' : '锁定地点'}</button></div>)}</div>
        </> : <div className="agent-plan-empty"><h3>计划会出现在这里</h3><p>会展示每天的景点、食宿、交通和待确认条件。</p></div>}
        {state?.attention_reason && <p className="planning-muted">{ATTENTION[state.attention_reason] ?? `状态原因：${state.attention_reason}`}</p>}
      </section>
    </div>
  </section>;
}
