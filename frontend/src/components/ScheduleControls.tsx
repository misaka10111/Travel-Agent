import { useEffect, useState } from 'react';
import type { SessionView } from '../api/planning';

type Preferences = SessionView['state']['intent_snapshot']['preferences'];
export type SchedulePatch = Pick<Preferences, 'breakfast_required' | 'compact_nearby' | 'day_start_time' | 'day_end_time'>;

export function ScheduleControls({ preferences, disabled, onApply }: {
  preferences: Preferences; disabled: boolean; onApply: (patch: SchedulePatch) => void;
}) {
  const [start, setStart] = useState(preferences.day_start_time?.slice(0, 5) ?? '');
  const [end, setEnd] = useState(preferences.day_end_time?.slice(0, 5) ?? '');
  const [breakfast, setBreakfast] = useState(preferences.breakfast_required ?? false);
  const [compact, setCompact] = useState(preferences.compact_nearby ?? false);
  useEffect(() => {
    setStart(preferences.day_start_time?.slice(0, 5) ?? '');
    setEnd(preferences.day_end_time?.slice(0, 5) ?? '');
    setBreakfast(preferences.breakfast_required ?? false);
    setCompact(preferences.compact_nearby ?? false);
  }, [preferences.day_start_time, preferences.day_end_time, preferences.breakfast_required, preferences.compact_nearby]);
  const valid = !start || !end || end > start;
  return <details className="ta-schedule-controls"><summary>调整每天的时间与用餐</summary>
    <div className="ta-schedule-fields">
      <label>开始时间<input type="time" value={start} onChange={event => setStart(event.target.value)} /></label>
      <label>结束时间<input type="time" value={end} onChange={event => setEnd(event.target.value)} /></label>
      <label><input type="checkbox" checked={breakfast} onChange={event => setBreakfast(event.target.checked)} /> 每天安排早餐</label>
      <label><input type="checkbox" checked={compact} onChange={event => setCompact(event.target.checked)} /> 邻近景点尽量同一天</label>
    </div>
    <p>时间留空时沿用偏好：早起 07:30、普通 09:00；结束默认 20:00。早餐地点仍需核实。</p>
    {!valid && <p role="alert">结束时间必须晚于开始时间。</p>}
    <button type="button" disabled={disabled || !valid} onClick={() => onApply({
      day_start_time: start || null, day_end_time: end || null, breakfast_required: breakfast, compact_nearby: compact,
    })}>应用并更新行程</button>
  </details>;
}
