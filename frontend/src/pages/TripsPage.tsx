import { Link } from 'react-router-dom';
import { api } from '../api/client';
import { useApi } from '../hooks/useApi';

export function TripsPage() {
  const userId = localStorage.getItem('currentUser') || '';
  const { data: memories, loading, error } = useApi(
    () => (userId ? api.listTripMemory(userId) : Promise.resolve([])),
    [userId],
  );

  return (
    <section className="trip-memory-page">
      <div className="trip-memory-page-header">
        <div>
          <span className="ta-section-kicker">TRIP HISTORY</span>
          <h1>历史行程</h1>
        </div>
      </div>

      {!userId && <p className="muted">请先登录后查看历史行程。</p>}
      {userId && loading && <p>加载中…</p>}
      {userId && error && <p className="error">{error}</p>}

      {userId && !loading && (!memories || memories.length === 0) && (
        <p className="muted">还没有历史行程记录。去 AI 助手确认一个计划后，会自动保存在这里。</p>
      )}

      <div className="card-grid trip-memory-grid">
        {memories?.map((memory) => (
          <Link
            className="trip-card trip-memory-card"
            key={memory.id}
            to={`/trips/${memory.id}`}
          >
            <div className="trip-memory-card-topline">
              <span className="trip-memory-card-status">已确认</span>
              <span className="trip-memory-card-date">
                {new Date(memory.created_at).toLocaleDateString('zh-CN')}
              </span>
            </div>
            <h2>{memory.destination || '未命名行程'}</h2>
            <p className="dates">
              {memory.start_date} ~ {memory.end_date}
            </p>
            <p className="trip-memory-card-style">
              方案：{memory.chosen_plan_style || '未选择'}
            </p>
            {memory.rating != null && <p>评分：{memory.rating} / 5</p>}
            {memory.feedback ? <p className="trip-memory-card-feedback">“{memory.feedback}”</p> : null}
            <span className="trip-memory-card-action">查看详细计划 →</span>
          </Link>
        ))}
      </div>
    </section>
  );
}
