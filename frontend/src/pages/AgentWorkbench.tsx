import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { useLocation } from 'react-router-dom';
import { planning } from '../api/planning';
import type { Draft, Place, Route, SessionCredential, SessionView } from '../api/planning';
import { BudgetCard } from '../components/BudgetCard';
import './agent-workbench.css';

type Message = { id: string; role: 'user' | 'assistant'; text: string };
type Category = 'transport' | 'hotel' | 'attraction';
type Snapshot = { plan: Draft; places: Place[] };
const SAVED = 'travelPlanningSession';
const CATEGORY: Record<Category, string> = { transport: '交通', hotel: '住宿', attraction: '景点与活动' };
const MODE: Record<string, string> = { walking: '步行', transit: '公交/地铁', driving: '驾车' };
const STATUS: Record<string, string> = { draft: '待规划', running: '正在规划', waiting_user: '需要补充信息', needs_attention: '需要调整', completed: '已生成草案', cancelled: '已取消', failed: '暂未完成' };
const REASON: Record<string, string> = {
  no_progress_repeated_action: '暂时没有找到新的可用地点，可以更换景点或调整条件。',
  search_stalled_no_new_candidates: '暂时没有找到新的可用地点，可以更换景点或调整条件。',
  server_shutdown_resume_available: '规划中断了，已保留需求和选择，可以继续生成。',
  model_request_failed: '规划服务暂时繁忙，请稍后继续。',
  budget_exhausted_or_obsolete: '这次尝试未能完成，可以保留选择重新生成。',
  plan_requires_refresh_or_wrong_ref: '行程发生了变化，请重新生成后查看。',
};
const ISSUE: Record<string, string> = {
  nearby_meals_required: '部分景点附近还缺少合适的用餐地点。',
  meals_missing: '当天的用餐地点还没有补齐。',
  route_unavailable: '部分地点之间的交通路线还没有查到。',
  route_missing: '部分地点之间还没有交通路线。',
  duplicate_attraction: '同一景点在不同日期重复出现，需要重新安排。',
  opening_unknown: '景点开放时间还需要核实。',
  evidence_missing: '部分价格、开放时间或服务信息尚待核实。',
  hotel_commute_long: '住宿到当天景点的往返交通较久，建议改选更靠近主要游览区域的酒店。',
};
function friendlyError(value: unknown) {
  const text = value instanceof Error ? value.message : String(value);
  if (text.includes('stale_state')) return '页面中的行程刚刚更新，请稍后再试。';
  if (text.includes('session_already_running')) return '新行程正在生成，请稍候。';
  if (text.includes('budget_exhausted')) return '这次尝试未能完成，请保留选择重新生成。';
  return '操作暂时没有成功，请稍后再试。';
}
function time(value: number | null) {
  return value === null ? '时间待定' : `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
}
function duration(seconds: number) {
  return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}
function spendReference(place: Place) {
  const fact = place.facts.average_spend_cny;
  const value = Number(fact?.value);
  return fact?.value != null && fact.source_ref.startsWith('amap:poi:') && Number.isFinite(value) && value > 0 ? value : null;
}
function saved<T>(key: string, fallback: T): T {
  try { return JSON.parse(localStorage.getItem(key) ?? 'null') ?? fallback; } catch { return fallback; }
}
function routeLabel(route: Route) {
  if (route.status !== 'ok') {
    const reasons: Record<string, string> = {
      nearby_meal_coverage_required: '景点附近的用餐地点还没补齐，路线查询暂缓',
      route_phase_budget_exhausted: '本次规划可用的地图查询次数已用完，路线待补查',
      budget_exhausted: '本次规划可用的地图查询次数已用完，路线待补查',
      rate_limited: '地图服务暂时限制查询，请稍后补查',
      unavailable: '地图服务暂时不可用，请稍后补查',
      permission_denied: '地图服务暂未授权这段路线',
      provider_returned_no_route: '地图服务没有找到可用路线',
      provider_missing_duration_or_distance: '地图服务未返回完整的耗时或距离',
      unsupported: '当前交通方式暂不支持这段路线',
    };
    return reasons[route.unknown_reason ?? ''] ?? '这段路线缺少可核实的交通数据';
  }
  const lines = route.steps.map(step => step.line_name).filter(Boolean).join(' → ');
  const minutes = Math.ceil((route.duration_seconds ?? 0) / 60);
  return `${MODE[route.mode] ?? '交通'}约 ${minutes} 分钟 · ${((route.distance_meters ?? 0) / 1000).toFixed(1)} 公里${lines ? ` · ${lines}` : ''}`;
}

function RouteSketch({ plan, dayIndex, places, focusPlace, onFocus }: {
  plan: Draft; dayIndex: number; places: Map<string, Place>; focusPlace: string | null; onFocus: (id: string) => void;
}) {
  const day = plan.days.find(item => item.day_index === dayIndex);
  if (!day) return <div className="ta-map-empty">这一天还没有可显示的地点。</div>;
  const stops = day.stops.map(stop => ({ stop, place: places.get(stop.place_id) }))
    .filter((entry): entry is { stop: typeof day.stops[number]; place: Place } =>
      !!entry.place?.coordinates && entry.place.coordinates.crs === 'GCJ02');
  if (!stops.length) return <div className="ta-map-empty">这些地点的坐标还需要重新查询。</div>;
  const traces = day.routes.filter(route => route.status === 'ok' && route.geometry?.crs === 'GCJ02' && route.geometry.encoding === 'points')
    .map(route => route.geometry!.points).filter(points => points.length > 1);
  const points = [...stops.map(item => item.place.coordinates!), ...traces.flat()];
  const middleLatitude = points.reduce((sum, point) => sum + point.latitude, 0) / points.length;
  const cosine = Math.cos(middleLatitude * Math.PI / 180);
  const xs = points.map(point => point.longitude * cosine);
  const ys = points.map(point => point.latitude);
  const minX = Math.min(...xs); const maxX = Math.max(...xs);
  const minY = Math.min(...ys); const maxY = Math.max(...ys);
  const scale = Math.min(560 / Math.max(maxX - minX, .002), 250 / Math.max(maxY - minY, .002));
  const offsetX = (640 - (maxX - minX) * scale) / 2;
  const offsetY = (330 - (maxY - minY) * scale) / 2;
  const position = (point: { longitude: number; latitude: number }) => ({
    x: offsetX + (point.longitude * cosine - minX) * scale,
    y: offsetY + (maxY - point.latitude) * scale,
  });
  return <div className="ta-map-sketch"><svg viewBox="0 0 640 330" role="img" aria-label={`第 ${dayIndex} 天的地点位置示意图`}>
    <defs><pattern id="trip-map-grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M 40 0 L 0 0 0 40" fill="none" stroke="#e5eaf3" strokeWidth="1" /></pattern></defs>
    <rect width="640" height="330" fill="#f5f7fb" /><rect width="640" height="330" fill="url(#trip-map-grid)" />
    {traces.map((trace, index) => <polyline key={index} points={trace.map(point => {
      const at = position(point); return `${at.x},${at.y}`;
    }).join(' ')} fill="none" stroke="#697be8" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />)}
    {stops.map(({ stop, place }, index) => {
      const at = position(place.coordinates!);
      const focused = focusPlace === place.place_id;
      return <g key={stop.stop_id} onClick={() => onFocus(place.place_id)} className="ta-sketch-marker">
        <title>{place.name}</title><circle cx={at.x} cy={at.y} r={focused ? 15 : 12} fill={stop.category === 'hotel' ? '#5969d6' : '#e97856'} stroke="white" strokeWidth="3" />
        <text x={at.x} y={at.y + 4} textAnchor="middle" fill="white" fontSize="11" fontWeight="700">{stop.category === 'hotel' ? 'H' : index + 1}</text>
      </g>;
    })}
  </svg><span>位置示意 · 仅蓝线为已查到的真实路线</span></div>;
}

export function AgentWorkbench() {
  const location = useLocation();
  const [draft, setDraft] = useState('');
  const [view, setView] = useState<SessionView | null>(null);
  const [credential, setCredential] = useState<SessionCredential | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [busy, setBusy] = useState(false);
  const [updateStartedAt, setUpdateStartedAt] = useState<number | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [lastDuration, setLastDuration] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [category, setCategory] = useState<Category>('attraction');
  const [dayIndex, setDayIndex] = useState(1);
  const [focusPlace, setFocusPlace] = useState<string | null>(null);
  const [mapUrl, setMapUrl] = useState<string | null>(null);
  const [mapNotice, setMapNotice] = useState('选择一天，查看地点与已查到的路线。');
  const mapUrlRef = useRef<string | null>(null);
  const initialized = useRef(false);
  const observed = useRef('');
  const state = view?.state;
  const timerKey = credential ? `travelPlanStarted:${credential.id}` : null;
  const running = state?.status === 'running';
  const canRetry = state?.status === 'needs_attention' && state.retry_generation === 0 && !state.retry_child_ref;
  const visible = snapshot ?? (state?.current_plan ? { plan: state.current_plan, places: state.candidates } : null);
  const plan = visible?.plan;
  const places = new Map(visible?.places.map(place => [place.place_id, place]) ?? []);
  const name = (id: string) => places.get(id)?.name ?? '地点待重新查询';
  const refreshing = !!snapshot && (busy || running || !!state?.plan_needs_refresh || !state?.current_plan);
  const say = (text: string, role: Message['role'] = 'assistant') =>
    setMessages(items => [...items, { id: crypto.randomUUID(), role, text }]);

  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    let previous: SessionCredential | null = null;
    try { previous = JSON.parse(sessionStorage.getItem(SAVED) ?? 'null'); } catch { previous = null; }
    if (previous) {
      setCredential(previous);
      planning.get(previous).then(next => { setView(next); setError(''); }).catch(() => {
        setError('上次行程暂时无法恢复，请重新描述需求。');
        sessionStorage.removeItem(SAVED); setCredential(null);
      });
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
        setView(next); setError('');
        if (next.state.status === 'running') timer = setTimeout(poll, 1500);
      } catch { if (active) { setError('更新状态暂时失败，正在重试。'); timer = setTimeout(poll, 4000); } }
    };
    timer = setTimeout(poll, 1000);
    return () => { active = false; clearTimeout(timer); };
  }, [credential, running]);

  useEffect(() => {
    if (busy || running) {
      if (updateStartedAt === null) {
        const savedStart = timerKey ? Number(sessionStorage.getItem(timerKey)) : NaN;
        const start = Number.isFinite(savedStart) && savedStart > 0 && Date.now() - savedStart < 3600000 ? savedStart : Date.now();
        setUpdateStartedAt(start); setElapsedSeconds(Math.floor((Date.now() - start) / 1000));
        if (timerKey) sessionStorage.setItem(timerKey, String(start));
        return;
      }
      if (timerKey) sessionStorage.setItem(timerKey, String(updateStartedAt));
      const timer = setInterval(() => setElapsedSeconds(Math.floor((Date.now() - updateStartedAt) / 1000)), 1000);
      return () => clearInterval(timer);
    }
    if (!state) return;
    if (timerKey) sessionStorage.removeItem(timerKey);
    if (updateStartedAt !== null) {
      setLastDuration(Math.floor((Date.now() - updateStartedAt) / 1000));
      setUpdateStartedAt(null);
    }
  }, [busy, running, updateStartedAt, timerKey, !!state]);

  useEffect(() => {
    if (state?.current_plan && state.status !== 'running' && !state.plan_needs_refresh) {
      setSnapshot({ plan: state.current_plan, places: state.candidates });
    }
  }, [state]);

  useEffect(() => {
    if (!state || state.status === 'running' || state.status === 'draft') return;
    const key = `${state.session_id}:${state.state_version}`;
    if (observed.current === key) return;
    observed.current = key;
    const question = state.pending_questions[0];
    if (question) say(question.prompt);
    else if (state.status === 'completed') say('行程草案已更新。请查看每天的地点和交通；尚未核实的内容会单独标明。');
    else if (state.status === 'needs_attention') say(REASON[state.attention_reason ?? ''] ?? '行程暂时未能完成，可以调整选择后继续。');
  }, [state]);

  useEffect(() => {
    if (!credential || !plan || (running && snapshot)) return;
    let active = true;
    planning.mapDay(credential, dayIndex).then(result => {
      if (!active) return;
      const next = URL.createObjectURL(result.blob);
      if (mapUrlRef.current) URL.revokeObjectURL(mapUrlRef.current);
      mapUrlRef.current = next;
      setMapUrl(next);
      setMapNotice(result.total > result.shown ? '地图显示部分已查到的路线；全部交通段请看下方行程。' : '地图展示当天地点及已查到的实际路线。');
    }).catch(() => { if (active) {
      setMapUrl(null);
      setMapNotice(state?.plan_needs_refresh ? '底图和路线正在重新查询；当前显示已知地点的位置示意。' :
        '底图暂时无法加载；当前显示已知地点的位置示意。');
    } });
    return () => { active = false; };
  }, [credential, plan?.plan_id, plan?.version, dayIndex, running, snapshot, state?.plan_needs_refresh]);

  useEffect(() => () => { if (mapUrlRef.current) URL.revokeObjectURL(mapUrlRef.current); }, []);

  async function run(access: SessionCredential, current: SessionView) {
    setView(await planning.run(access, current.state.state_version));
  }
  async function submit(text: string, optionId?: string) {
    if (busy || running || !text.trim()) return;
    setBusy(true); setError(''); say(text, 'user'); setDraft('');
    try {
      let current = view;
      let access = credential;
      if (!current || !access) {
        const raw = saved<Record<string, unknown>>('userProfile', {});
        const profile = Object.fromEntries(['age_group', 'gender', 'identity', 'city', 'travel_style', 'preferences']
          .filter(key => raw[key] !== undefined).map(key => [key, raw[key]]));
        const created = await planning.create(text, profile);
        access = { id: created.state.session_id, token: created.access_token };
        sessionStorage.setItem(SAVED, JSON.stringify(access)); setCredential(access); current = created;
      } else if (current.state.pending_questions[0]) {
        current = await planning.answer(access, current.state.pending_questions[0], optionId ? undefined : text, optionId);
      } else {
        current = await planning.edit(access, current.state.state_version, { message: text });
      }
      setView(current);
      await run(access, current);
    } catch (cause) {
      setError(friendlyError(cause));
      if (credential) planning.get(credential).then(setView).catch(() => undefined);
    } finally { setBusy(false); }
  }
  async function resume() {
    if (!credential || !state || busy || running) return;
    setBusy(true); setError('');
    try { await run(credential, view!); } catch (cause) { setError(friendlyError(cause)); }
    finally { setBusy(false); }
  }
  async function retry() {
    if (!credential || !state || busy || !canRetry) return;
    const started = Date.now();
    setBusy(true); setError('');
    try {
      const created = await planning.retry(credential, state.state_version);
      const access = { id: created.state.session_id, token: created.access_token };
      if (timerKey) sessionStorage.removeItem(timerKey);
      setUpdateStartedAt(started); setElapsedSeconds(0);
      sessionStorage.setItem(SAVED, JSON.stringify(access)); setCredential(access); setView(created);
      say('已保留本次需求与选择，正在重新生成行程。');
      await run(access, created);
    } catch (cause) { setError(friendlyError(cause)); }
    finally { setBusy(false); }
  }
  async function choose(place: Place) {
    if (!credential || !state || running || busy) return;
    const selected = state.selections.some(item => item.place_id === place.place_id && item.decision === 'lock');
    const selections = state.selections.filter(item => item.place_id !== place.place_id &&
      !(place.category === 'hotel' && state.candidates.some(candidate => candidate.place_id === item.place_id && candidate.category === 'hotel')));
    if (!selected) selections.push({ selection_id: crypto.randomUUID(), place_id: place.place_id, decision: 'lock',
      evidence: { source: 'selection', source_ref: 'workbench-card', confirmation: 'explicit' } });
    setBusy(true); setError('');
    try {
      const edited = await planning.edit(credential, state.state_version, { selections });
      setView(edited);
      say(selected ? `已移除${place.name}，正在调整行程。` : `已选择${place.name}，正在调整行程。`);
      await run(credential, edited);
    } catch (cause) { setError(friendlyError(cause)); planning.get(credential).then(setView).catch(() => undefined); }
    finally { setBusy(false); }
  }
  async function cancel() {
    if (!credential || !state) return;
    try { setView(await planning.cancel(credential, state.state_version)); }
    catch (cause) { setError(friendlyError(cause)); }
  }
  function newTrip() {
    if (timerKey) sessionStorage.removeItem(timerKey);
    sessionStorage.removeItem(SAVED); setCredential(null); setView(null); setSnapshot(null);
    setUpdateStartedAt(null); setElapsedSeconds(0); setLastDuration(null);
    setMessages([]); setError(''); setMapUrl(null); observed.current = '';
  }
  function onSubmit(event: FormEvent) { event.preventDefault(); void submit(draft); }

  const offers = plan?.travel_offers.filter(offer => category === 'transport' && (offer.category === 'flight' || offer.category === 'train')) ?? [];
  const selectedDay = plan?.days.find(day => day.day_index === dayIndex) ?? plan?.days[0];
  const warnings = selectedDay ? plan?.audit?.issues.filter(issue => issue.day_index === selectedDay.day_index &&
    (issue.severity !== 'warning' || issue.code === 'hotel_commute_long')) ?? [] : [];
  const hotelRecommendations = (plan?.recommendations ?? []).filter(item => item.category === 'hotel');
  const hotelOrder = new Map(hotelRecommendations.map((item, index) => [item.place_id, index]));

  return <section className="travel-agent-workbench">
    <div className="travel-agent-layout">
      <aside className="ta-chat-panel">
        <header className="ta-panel-header"><span>旅行助手</span><h2>聊聊你的旅行</h2>
          <small>{state ? STATUS[state.status] ?? '规划中' : '等待输入'}</small></header>
        <div className="ta-chat-messages" aria-live="polite">
          {!messages.length && <p className="ta-empty-chat">例如：从上海去北京玩 5 天，喜欢古建筑，住宿交通便利，饮食清淡。</p>}
          {messages.map(message => <div className={`ta-message ${message.role}`} key={message.id}>{message.text}</div>)}
          {(running || busy) && <div className="ta-message assistant">正在整理地点与路线，已等待 {duration(elapsedSeconds)}；右侧现有行程会继续显示。</div>}
        </div>
        {state?.pending_questions[0]?.options.map(option => <button className="ta-option-answer" key={option.option_id}
          disabled={busy || running} onClick={() => void submit(option.label, option.option_id)}>{option.label}</button>)}
        <form className="ta-chat-composer" onSubmit={onSubmit}>
          <textarea aria-label="旅行需求" value={draft} onChange={event => setDraft(event.target.value)} rows={3}
            placeholder="描述旅行需求，或修改当前行程" onKeyDown={event => {
              if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); }
            }} />
          <button type="submit" disabled={busy || running || !draft.trim()}>发送</button>
        </form>
        <div className="ta-chat-actions">
          {state && (['draft', 'needs_attention'].includes(state.status) || state.plan_needs_refresh) && !running &&
            <button onClick={() => void resume()} disabled={busy}>继续规划</button>}
          {canRetry && !running && <button onClick={() => void retry()} disabled={busy}>保留选择，重新生成</button>}
          {running && <button onClick={() => void cancel()}>暂停本次规划</button>}
          {!running && <button onClick={newTrip} disabled={busy}>新旅行</button>}
        </div>
      </aside>

      <section className="ta-options-panel">
        <header className="ta-panel-header"><span>候选方案</span><h2>选择你喜欢的安排</h2>
          <p>选择地点后会在后台调整行程，右侧旧行程继续可看。</p></header>
        <div className="ta-category-tabs">{(['transport', 'hotel', 'attraction'] as Category[]).map(item =>
          <button key={item} className={category === item ? 'active' : ''} onClick={() => setCategory(item)}>{CATEGORY[item]}</button>)}</div>
        <div className="ta-candidate-lanes">
          {(['transport', 'hotel', 'attraction'] as Category[]).map(group => <section key={group}
            className={`ta-candidate-lane ${category === group ? 'active' : ''}`}>
            <h3>{CATEGORY[group]}</h3>
            {group === 'transport' && (plan?.travel_offers ?? []).filter(offer => offer.category === 'train' || offer.category === 'flight').map((offer, index) =>
              <article className="ta-option-card" key={`${offer.name}-${index}`}>
                <strong>{offer.name ?? (offer.category === 'train' ? '火车候选' : '航班候选')}</strong>
                <p>{offer.segments.map(segment => `${segment.origin_station ?? '出发地待确认'} → ${segment.destination_station ?? '目的地待确认'}`).join(' / ')}</p>
                <small>{offer.price_display ? `供应商显示 ${offer.price_display} ${offer.currency ?? '（币种待确认）'} · ${offer.price_scope === 'ticket' ? '每张票' : '计价范围待确认'}` : '价格待查询'} · 尚未预订</small>
              </article>)}
            {state?.candidates.filter(place => place.category === group).sort((a, b) => group === 'hotel' ?
              (hotelOrder.get(a.place_id) ?? 999) - (hotelOrder.get(b.place_id) ?? 999) : 0).slice(0, 24).map(place => {
              const selected = state.selections.some(item => item.place_id === place.place_id && item.decision === 'lock');
              const hotelFit = group === 'hotel' ? hotelRecommendations.find(item => item.place_id === place.place_id) : undefined;
              return <article className={`ta-option-card ${selected ? 'selected' : ''}`} key={place.place_id}>
                <strong>{place.name}</strong><p>{place.address ?? '地址待确认'}</p>
                {hotelFit && <small>{hotelOrder.get(place.place_id) === 0 ? '当前行程优先推荐 · ' : '多日位置参考 · '}{hotelFit.reasons[0]}</small>}
                {group === 'attraction' && spendReference(place) !== null && <small>高德人均消费参考 ¥{spendReference(place)}；不代表门票报价</small>}
                <small>{group === 'hotel' ? '房价和空房需按日期查询' : group === 'attraction' ? '开放与预约信息待核实' : '出行方式待确认'}</small>
                {group !== 'transport' && <button type="button" disabled={busy || running} onClick={() => void choose(place)}>
                  {selected ? '从行程移除' : '选入并更新行程'}</button>}
              </article>;
            })}
            {group === 'transport' && !offers.length && !state?.candidates.some(place => place.category === 'transport') &&
              <p className="ta-empty-options">{state?.intent_snapshot.dates.start_date ? '当前还没有可核实的车次或航班。' : '确定出发日期后，可查询车次或航班。'}</p>}
            {group !== 'transport' && !state?.candidates.some(place => place.category === group) &&
              <p className="ta-empty-options">{group === 'hotel' ? '住宿候选还在查询。' : '景点候选还在查询。'}</p>}
          </section>)}
        </div>
      </section>

      <aside className="ta-right-panel">
        <section className="ta-map-card">
          <header className="ta-panel-header"><span>行程地图</span><h2>{state?.intent_snapshot.destination.label ?? '目的地'} · 第 {dayIndex} 天</h2></header>
          {plan && <div className="ta-day-tabs">{plan.days.map(day => <button key={day.day_index}
            className={dayIndex === day.day_index ? 'active' : ''} onClick={() => { setDayIndex(day.day_index); setFocusPlace(null); setMapUrl(null); }}>{day.day_index}</button>)}</div>}
          {mapUrl ? <img className="ta-map-image" src={mapUrl} alt={`第 ${dayIndex} 天的高德地点与路线地图`} /> : plan ?
            <RouteSketch plan={plan} dayIndex={dayIndex} places={places} focusPlace={focusPlace} onFocus={setFocusPlace} /> :
            <div className="ta-map-empty">行程生成后会显示地点与路线地图。</div>}
          <p className="ta-map-caption">{mapNotice}</p>
          {focusPlace && <p className="ta-map-focus">当前关注：{name(focusPlace)}</p>}
        </section>
        <section className="ta-plan-card">
          <header className="ta-panel-header"><span>旅行计划</span><h2>{plan ? `${plan.days.length} 天行程` : '等待行程'}</h2></header>
          {refreshing && <div className="ta-update-banner" role="status"><strong>{running ? '新行程正在更新' : '新行程尚未生成'}</strong>
            <span>{busy || running ? `已等待 ${duration(elapsedSeconds)}；下面仍是上一版，完成后自动替换。` : '下面仍是上一版，可调整选择后继续规划。'}</span></div>}
          {error && <div className="ta-error" role="alert">{error}</div>}
          {state && <BudgetCard key={state.session_id} plan={plan ?? null} intent={state.intent_snapshot} sessionId={state.session_id} />}
          {!plan ? <p className="ta-plan-empty">选择景点或描述需求后，这里会展示每天的地点和交通。</p> : <>
            <p className="ta-plan-context">{plan.status === 'verified' && !refreshing ? '已核实的行程草案' : '行程草案，部分信息待确认'} · 尚未预订
              {!busy && !running && lastDuration !== null && ` · 上次处理用时 ${duration(lastDuration)}`}</p>
            {selectedDay && <div className="ta-day-plan"><h3>第 {selectedDay.day_index} 天 {selectedDay.date ?? '日期待定'}</h3>
              {selectedDay.stops.map((stop, index) => {
                const before = selectedDay.stops[index - 1];
                const occurrence = before ? selectedDay.stops.slice(1, index).filter((prior, offset) =>
                  selectedDay.stops[offset].place_id === before.place_id && prior.place_id === stop.place_id).length : 0;
                const route = before && selectedDay.routes.filter(item => item.from_place_id === before.place_id && item.to_place_id === stop.place_id)[occurrence];
                return <div key={stop.stop_id}>
                  {before && <p className={`ta-route ${route?.status === 'ok' ? '' : 'unknown'}`}>{route ? routeLabel(route) : '这段路线尚未生成'}</p>}
                  <button className={`ta-plan-stop ${focusPlace === stop.place_id ? 'focused' : ''}`} onClick={() => setFocusPlace(stop.place_id)}>
                    <span>{time(stop.start_minute)} · {stop.category === 'attraction' ? '景点' : stop.category === 'restaurant' ? '用餐' : stop.category === 'hotel' ? '住宿' : '交通'}</span>
                    <strong>{name(stop.place_id)} {stop.locked ? '✓' : ''}</strong>
                    <small>{places.get(stop.place_id)?.address ?? '地点详情待确认'}</small>
                    {!!stop.child_place_ids.length && <small>同一景区内：{stop.child_place_ids.map(name).join('、')}</small>}
                  </button>
                </div>;
              })}
              {!!warnings.length && <details className="ta-day-notes"><summary>这一天还需确认</summary>
                {[...new Set(warnings.map(issue => ISSUE[issue.code] ?? '还有一项安排需要核实。'))].map(text => <p key={text}>{text}</p>)}
              </details>}
            </div>}
            {!!plan.missing_requirements.length && <details className="ta-day-notes"><summary>出发前还需确认</summary>
              {plan.missing_requirements.filter(text => !/[a-z]{3,}_/.test(text)).map(text => <p key={text}>{text}</p>)}
            </details>}
            {state?.attention_reason && !running && <p className="ta-plan-context">{REASON[state.attention_reason] ?? '行程还需要调整，当前内容仅供参考。'}</p>}
          </>}
        </section>
      </aside>
    </div>
  </section>;
}
