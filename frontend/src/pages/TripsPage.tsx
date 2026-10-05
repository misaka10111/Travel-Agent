import { api } from '../api/client';
import { useApi } from '../hooks/useApi';

export function TripsPage() {
  const userId = localStorage.getItem('currentUser') || '';
  const { data: memories, loading, error } = useApi(() =>
    api.listTripMemory(userId),
  );

  return (
    <section>
      <h1>历史行程</h1>

      {loading && <p>加载中…</p>}
      {error && <p className="error">{error}</p>}

      {!loading && (!memories || memories.length === 0) && (
        <p>还没有历史行程记录。</p>
      )}

      <div className="card-grid">
        {memories?.map((memory, index) => (
          <article className="trip-card" key={String(memory.id ?? index)}>
            <h2>{String(memory.destination ?? '未命名行程')}</h2>
            <p>
              {String(memory.start_date ?? '')} ~ {String(memory.end_date ?? '')}
            </p>
            <p>方案：{String(memory.chosen_plan_style ?? '未选择')}</p>
            {memory.rating != null && <p>评分：{String(memory.rating)}</p>}
            {memory.feedback ? <p>评价：{String(memory.feedback)}</p> : null}
          </article>
        ))}
      </div>
    </section>
  );
}
