import { useMemo, useState } from 'react';
import type { KeyboardEvent } from 'react';
import { api } from '../api/client';

type ChatMessage = {
  id: string;
  role: 'assistant' | 'user';
  content: string;
};

type OptionType = 'flight' | 'hotel' | 'spot';

type BaseOption = {
  id: string;
  title: string;
  subtitle: string;
  scheduleAt: string;
  scheduleLabel: string;
  location: string;
  tags: string[];
  description: string;
};

type FlightOption = BaseOption & {
  type: 'flight';
  from: string;
  to: string;
  departTime: string;
  arriveTime: string;
  airline: string;
  duration: string;
  price: number;
};

type HotelOption = BaseOption & {
  type: 'hotel';
  district: string;
  checkIn: string;
  checkOut: string;
  roomType: string;
  rating: number;
  nightlyPrice: number;
  totalPrice: number;
};

type SpotOption = BaseOption & {
  type: 'spot';
  area: string;
  openHours: string;
  recommendedDuration: string;
  ticketPrice: number;
};

type OptionItem = FlightOption | HotelOption | SpotOption;

function parsePrice(value: unknown): number {
  const n = parseFloat(String(value ?? '').replace(/[^0-9.]/g, ''));
  return Number.isFinite(n) ? n : 0;
}

function mapFlights(items: unknown): FlightOption[] {
  return ((items as unknown[]) || []).map((f: any, i: number) => ({
    id: `flight-${i}`,
    type: 'flight',
    title: `${f?.airline ?? ''}${f?.flight_no ?? ''}`,
    subtitle: `${f?.dep_station ?? ''} → ${f?.arr_station ?? ''}`,
    from: f?.dep_station ?? '',
    to: f?.arr_station ?? '',
    departTime: f?.dep_time ?? '',
    arriveTime: f?.arr_time ?? '',
    airline: f?.airline ?? '',
    duration: f?.duration ?? '',
    price: parsePrice(f?.price),
    scheduleAt: f?.dep_time ?? '',
    scheduleLabel: f?.dep_time ?? '',
    location: f?.arr_station ?? '',
    tags: f?.seat ? [f.seat] : [],
    description: `${f?.airline ?? ''}${f?.flight_no ?? ''}，${f?.dep_station ?? ''} → ${f?.arr_station ?? ''}`,
  }));
}

function mapHotels(items: unknown): HotelOption[] {
  return ((items as unknown[]) || []).map((h: any, i: number) => {
    const price = parsePrice(h?.price);
    return {
      id: `hotel-${i}`,
      type: 'hotel',
      title: h?.name ?? '',
      subtitle: h?.location ?? '',
      district: h?.location ?? '',
      checkIn: '',
      checkOut: '',
      roomType: h?.star ?? '',
      rating: parseFloat(String(h?.score ?? 0)) || 0,
      nightlyPrice: price,
      totalPrice: price,
      scheduleAt: '',
      scheduleLabel: '',
      location: h?.location ?? '',
      tags: h?.star ? [h.star] : [],
      description: `${h?.name ?? ''}，${h?.star ?? ''}，${h?.location ?? ''}`,
    };
  });
}

function mapSpots(items: unknown): SpotOption[] {
  return ((items as unknown[]) || []).map((p: any, i: number) => ({
    id: `spot-${i}`,
    type: 'spot',
    title: p?.name ?? '',
    subtitle: p?.category ?? '',
    area: p?.category ?? '',
    openHours: '',
    recommendedDuration: '',
    ticketPrice: 0,
    scheduleAt: '',
    scheduleLabel: '',
    location: '',
    tags: p?.rank ? [p.rank] : [],
    description: p?.description ?? '',
  }));
}

const formatPrice = (price: number) => `¥ ${price.toLocaleString()}`;
const buildId = () => `${Date.now()}-${Math.random()}`;

function getOptionPrice(item: OptionItem) {
  if (item.type === 'flight') return item.price;
  if (item.type === 'hotel') return item.totalPrice;
  return item.ticketPrice;
}

function getOptionIcon(type: OptionType) {
  if (type === 'flight') return '✈';
  if (type === 'hotel') return '▣';
  return '●';
}

function getOptionTypeLabel(type: OptionType) {
  if (type === 'flight') return '机票';
  if (type === 'hotel') return '酒店';
  return '景点 / 活动';
}

export function AgentPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: buildId(),
      role: 'assistant',
      content:
        '你好，告诉我你想去哪里、什么时候出发，我会为你搜索机票、酒店和景点。',
    },
  ]);

  const [draft, setDraft] = useState('');
  const [activeTab, setActiveTab] = useState<OptionType>('flight');
  const [planItemIds, setPlanItemIds] = useState<string[]>([]);
  const [detailItem, setDetailItem] = useState<OptionItem | null>(null);
  const [options, setOptions] = useState<OptionItem[]>([]);
  const [loading, setLoading] = useState(false);

  const visibleOptions: OptionItem[] = useMemo(() => {
    if (activeTab === 'flight') return options.filter((o) => o.type === 'flight');
    if (activeTab === 'hotel') return options.filter((o) => o.type === 'hotel');
    return options.filter((o) => o.type === 'spot');
  }, [activeTab, options]);

  const travelPlan = useMemo(
    () =>
      options
        .filter((item) => planItemIds.includes(item.id))
        .sort(
          (a, b) =>
            new Date(a.scheduleAt).getTime() - new Date(b.scheduleAt).getTime(),
        ),
    [planItemIds, options],
  );

  const totalBudget = useMemo(
    () => travelPlan.reduce((sum, item) => sum + getOptionPrice(item), 0),
    [travelPlan],
  );

  async function handleSend() {
    const content = draft.trim();
    if (!content || loading) return;

    setDraft('');
    setLoading(true);
    setMessages((prev) => [...prev, { id: buildId(), role: 'user', content }]);

    try {
      const raw = await api.search({ query: content });
      if (raw.error) throw new Error(String(raw.error));
      const data = raw as Record<string, unknown>;
      const flights = mapFlights(data.flights);
      const hotels = mapHotels(data.hotels);
      const spots = mapSpots(data.poi);
      setOptions([...flights, ...hotels, ...spots]);
      setPlanItemIds([]);
      setDetailItem(null);
      setMessages((prev) => [
        ...prev,
        {
          id: buildId(),
          role: 'assistant',
          content: `已为你找到 ${flights.length} 个航班、${hotels.length} 家酒店、${spots.length} 个景点，在右侧查看并加入旅行计划吧。`,
        },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          id: buildId(),
          role: 'assistant',
          content: `搜索失败：${err instanceof Error ? err.message : String(err)}`,
        },
      ]);
    } finally {
      setLoading(false);
    }
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      handleSend();
    }
  }

  function addToPlan(item: OptionItem) {
    setPlanItemIds((current) =>
      current.includes(item.id) ? current : [...current, item.id],
    );
  }

  function removeFromPlan(itemId: string) {
    setPlanItemIds((current) => current.filter((id) => id !== itemId));
  }

  function togglePlan(item: OptionItem) {
    if (planItemIds.includes(item.id)) {
      removeFromPlan(item.id);
    } else {
      addToPlan(item);
    }
  }

  function isInPlan(item: OptionItem) {
    return planItemIds.includes(item.id);
  }

  function renderOptionMeta(item: OptionItem) {
    if (item.type === 'flight') {
      return (
        <>
          <span>{item.scheduleLabel}</span>
          <span>{item.duration}</span>
          <span>{formatPrice(item.price)}</span>
        </>
      );
    }

    if (item.type === 'hotel') {
      return (
        <>
          <span>{item.scheduleLabel}</span>
          <span>{item.rating} ★</span>
          <span>{formatPrice(item.totalPrice)}</span>
        </>
      );
    }

    return (
      <>
        <span>{item.scheduleLabel}</span>
        <span>{item.recommendedDuration}</span>
        <span>{formatPrice(item.ticketPrice)}</span>
      </>
    );
  }

  return (
    <section className="travel-agent-workbench">
      <div className="travel-agent-layout">
        {/* 左侧：AI 对话 */}
        <aside className="ta-chat-panel">
          <div className="ta-panel-header">
            <div>
              <span className="ta-section-kicker">AI ASSISTANT</span>
              <h2>对话界面</h2>
            </div>
          </div>

          <div className="ta-chat-messages">
            {messages.map((message) => (
              <div
                key={message.id}
                className={`ta-message ${message.role === 'user' ? 'user' : 'assistant'}`}
              >
                {message.role === 'assistant' && (
                  <span className="ta-message-avatar">TR</span>
                )}
                <div className="ta-message-bubble">{message.content}</div>
              </div>
            ))}
          </div>

          <div className="ta-chat-composer">
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={handleComposerKeyDown}
              placeholder="继续补充需求…"
              rows={1}
            />
            <button
              type="button"
              className="ta-send-button"
              onClick={handleSend}
              disabled={!draft.trim()}
            >
              发送
            </button>
          </div>
        </aside>

        {/* 中间：可供选择的行程 */}
        <section className="ta-options-panel ta-options-column">
          <div className="ta-options-header">
            <div>
              <span className="ta-section-kicker">OPTIONS</span>
              <h2>可供选择的行程</h2>
            </div>

            <div className="ta-tabs">
              <button
                type="button"
                className={activeTab === 'flight' ? 'active' : ''}
                onClick={() => setActiveTab('flight')}
              >
                机票
              </button>
              <button
                type="button"
                className={activeTab === 'hotel' ? 'active' : ''}
                onClick={() => setActiveTab('hotel')}
              >
                酒店
              </button>
              <button
                type="button"
                className={activeTab === 'spot' ? 'active' : ''}
                onClick={() => setActiveTab('spot')}
              >
                景点 / 活动
              </button>
            </div>
          </div>

          <div className="ta-option-list">
            {visibleOptions.map((item) => {
              const added = isInPlan(item);

              return (
                <article
                  key={item.id}
                  className={`ta-option-card ${added ? 'selected' : ''}`}
                  onClick={() => setDetailItem(item)}
                >
                  <div className="ta-option-main">
                    <div className="ta-option-title-row">
                      <div>
                        <h3>{item.title}</h3>
                        <p>{item.subtitle}</p>
                      </div>

                      {added && (
                        <span className="ta-selected-badge">已加入计划</span>
                      )}
                    </div>

                    <div className="ta-option-meta">
                      {renderOptionMeta(item)}
                    </div>

                    <div className="ta-option-tags">
                      {item.tags.map((tag) => (
                        <span key={tag}>{tag}</span>
                      ))}
                    </div>
                  </div>

                  <div
                    className="ta-option-actions"
                    onClick={(event) => event.stopPropagation()}
                  >
                    <button
                      className="ta-ghost-button"
                      type="button"
                      onClick={() => setDetailItem(item)}
                    >
                      查看详情
                    </button>

                    <button
                      className={added ? 'ta-remove-button' : 'ta-primary-button'}
                      type="button"
                      onClick={() => togglePlan(item)}
                    >
                      {added ? '从计划移除' : '添加到计划'}
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        </section>

        {/* 右侧：上方地图，下方旅行计划 */}
        <aside className="ta-right-panel">
          <section className="ta-map-card">
            <div className="ta-panel-header ta-compact-header">
              <div>
                <span className="ta-section-kicker">MAP</span>
                <h2>地图</h2>
              </div>
            </div>

            <div className="ta-map-placeholder">
              <span>地图区域</span>
              <p>当前阶段预留</p>
            </div>
          </section>

          <section className="ta-plan-card">
            <div className="ta-panel-header ta-compact-header">
              <div>
                <span className="ta-section-kicker">TRAVEL PLAN</span>
                <h2>旅行计划</h2>
              </div>

              {travelPlan.length > 0 && (
                <div className="ta-plan-budget">
                  <span>预计</span>
                  <strong>{formatPrice(totalBudget)}</strong>
                </div>
              )}
            </div>

            {travelPlan.length === 0 ? (
              <div className="ta-plan-empty">
                从中间候选行程中添加机票、酒店或景点。
                <span>添加后会自动按时间排序。</span>
              </div>
            ) : (
              <div className="ta-plan-timeline">
                {travelPlan.map((item) => (
                  <div
                    className="ta-plan-row"
                    key={item.id}
                    role="button"
                    tabIndex={0}
                    onClick={() => setDetailItem(item)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        setDetailItem(item);
                      }
                    }}
                    aria-label={`查看 ${item.title} 的详细信息`}
                  >
                    <div className={`ta-plan-type ${item.type}`}>
                      {getOptionIcon(item.type)}
                    </div>

                    <div className="ta-plan-time">{item.scheduleLabel}</div>

                    <div className="ta-plan-content">
                      <strong>{item.title}</strong>
                      <span>
                        {getOptionTypeLabel(item.type)} · {item.location}
                      </span>
                    </div>

                    <button
                      type="button"
                      className="ta-plan-remove"
                      onClick={(event) => {
                        event.stopPropagation();
                        removeFromPlan(item.id);
                      }}
                      aria-label={`从旅行计划移除 ${item.title}`}
                    >
                      ×
                    </button>
                  </div>
                ))}
              </div>
            )}
          </section>
        </aside>
      </div>

      {detailItem && (
        <div className="ta-modal-backdrop" onClick={() => setDetailItem(null)}>
          <div
            className="ta-detail-modal"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="ta-detail-header">
              <div>
                <span className="ta-section-kicker">
                  {getOptionTypeLabel(detailItem.type)}
                </span>
                <h3>{detailItem.title}</h3>
                <p>
                  {detailItem.subtitle} · {detailItem.scheduleLabel}
                </p>
              </div>

              <button
                type="button"
                className="ta-close-button"
                onClick={() => setDetailItem(null)}
              >
                ×
              </button>
            </div>

            <div className="ta-detail-body">
              {detailItem.type === 'flight' && (
                <>
                  <div className="ta-detail-grid">
                    <div>
                      <span>出发</span>
                      <strong>{detailItem.from}</strong>
                    </div>
                    <div>
                      <span>到达</span>
                      <strong>{detailItem.to}</strong>
                    </div>
                    <div>
                      <span>起飞时间</span>
                      <strong>{detailItem.departTime}</strong>
                    </div>
                    <div>
                      <span>到达时间</span>
                      <strong>{detailItem.arriveTime}</strong>
                    </div>
                    <div>
                      <span>航司</span>
                      <strong>{detailItem.airline}</strong>
                    </div>
                    <div>
                      <span>飞行时长</span>
                      <strong>{detailItem.duration}</strong>
                    </div>
                  </div>

                  <div className="ta-detail-foot">
                    <span>位置：{detailItem.location}</span>
                    <strong>{formatPrice(detailItem.price)}</strong>
                  </div>
                </>
              )}

              {detailItem.type === 'hotel' && (
                <>
                  <div className="ta-detail-grid">
                    <div>
                      <span>区域</span>
                      <strong>{detailItem.district}</strong>
                    </div>
                    <div>
                      <span>房型</span>
                      <strong>{detailItem.roomType}</strong>
                    </div>
                    <div>
                      <span>入住</span>
                      <strong>{detailItem.checkIn}</strong>
                    </div>
                    <div>
                      <span>离店</span>
                      <strong>{detailItem.checkOut}</strong>
                    </div>
                    <div>
                      <span>评分</span>
                      <strong>{detailItem.rating} ★</strong>
                    </div>
                    <div>
                      <span>每晚均价</span>
                      <strong>{formatPrice(detailItem.nightlyPrice)}</strong>
                    </div>
                  </div>

                  <div className="ta-detail-foot">
                    <span>位置：{detailItem.location}</span>
                    <strong>{formatPrice(detailItem.totalPrice)}</strong>
                  </div>
                </>
              )}

              {detailItem.type === 'spot' && (
                <>
                  <div className="ta-detail-grid">
                    <div>
                      <span>区域</span>
                      <strong>{detailItem.area}</strong>
                    </div>
                    <div>
                      <span>开放时间</span>
                      <strong>{detailItem.openHours}</strong>
                    </div>
                    <div>
                      <span>建议时长</span>
                      <strong>{detailItem.recommendedDuration}</strong>
                    </div>
                    <div>
                      <span>门票</span>
                      <strong>{formatPrice(detailItem.ticketPrice)}</strong>
                    </div>
                  </div>

                  <div className="ta-detail-foot">
                    <span>位置：{detailItem.location}</span>
                    <strong>{detailItem.subtitle}</strong>
                  </div>
                </>
              )}

              <div className="ta-detail-description">
                <span>说明</span>
                <p>{detailItem.description}</p>
              </div>

              <div className="ta-modal-actions">
                <button
                  type="button"
                  className="ta-ghost-button"
                  onClick={() => setDetailItem(null)}
                >
                  关闭
                </button>

                <button
                  type="button"
                  className={
                    isInPlan(detailItem)
                      ? 'ta-remove-button'
                      : 'ta-primary-button'
                  }
                  onClick={() => togglePlan(detailItem)}
                >
                  {isInPlan(detailItem) ? '从计划移除' : '添加到计划'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

export default AgentPage;
