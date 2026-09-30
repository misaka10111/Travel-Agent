import { useMemo, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent as ReactPointerEvent } from 'react';
import { api } from '../api/client';
import { TripMap } from '../components/TripMap';
import type { RouteBlock, RouteLeg } from '../components/TripMap';

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

type RoutePlan = {
  destination: string;
  styles: string[];
  blocks: RouteBlock[];
  legs: RouteLeg[];
};

const flights: FlightOption[] = [
  {
    id: 'flight-1',
    type: 'flight',
    title: '国泰航空 CX420',
    subtitle: '香港 → 首尔',
    from: '香港 HKG',
    to: '首尔 ICN',
    departTime: '11/05 09:10',
    arriveTime: '11/05 13:35',
    airline: 'Cathay Pacific',
    duration: '3h 25m',
    price: 2180,
    scheduleAt: '2026-11-05T09:10:00',
    scheduleLabel: '11/05 · 09:10',
    location: '仁川国际机场',
    tags: ['直飞', '早班机', '推荐'],
    description: '直飞航班，时间友好，适合作为当前首选方案。',
  },
  {
    id: 'flight-2',
    type: 'flight',
    title: '大韩航空 KE608',
    subtitle: '香港 → 首尔',
    from: '香港 HKG',
    to: '首尔 GMP',
    departTime: '11/05 13:40',
    arriveTime: '11/05 18:05',
    airline: 'Korean Air',
    duration: '3h 25m',
    price: 1950,
    scheduleAt: '2026-11-05T13:40:00',
    scheduleLabel: '11/05 · 13:40',
    location: '金浦国际机场',
    tags: ['直飞', '性价比'],
    description: '价格更低，适合预算优先的路线选择。',
  },
  {
    id: 'flight-3',
    type: 'flight',
    title: '韩亚航空 OZ746',
    subtitle: '香港 → 首尔',
    from: '香港 HKG',
    to: '首尔 ICN',
    departTime: '11/06 08:20',
    arriveTime: '11/06 12:50',
    airline: 'Asiana Airlines',
    duration: '3h 30m',
    price: 2280,
    scheduleAt: '2026-11-06T08:20:00',
    scheduleLabel: '11/06 · 08:20',
    location: '仁川国际机场',
    tags: ['直飞', '时间稳定'],
    description: '到达时间早，便于第一天安排更多活动。',
  },
];

const hotels: HotelOption[] = [
  {
    id: 'hotel-1',
    type: 'hotel',
    title: 'L7 弘大酒店',
    subtitle: '首尔 · 弘大',
    district: '弘大商圈',
    checkIn: '11/05 15:00',
    checkOut: '11/09 11:00',
    roomType: '标准双床房',
    rating: 4.6,
    nightlyPrice: 680,
    totalPrice: 2720,
    scheduleAt: '2026-11-05T15:00:00',
    scheduleLabel: '11/05 · 15:00',
    location: '首尔市麻浦区',
    tags: ['交通方便', '年轻氛围', '推荐'],
    description: '靠近地铁和演出活动区域，适合喜欢 live music 的用户。',
  },
  {
    id: 'hotel-2',
    type: 'hotel',
    title: '九树明洞 2 号店',
    subtitle: '首尔 · 明洞',
    district: '明洞商圈',
    checkIn: '11/05 15:00',
    checkOut: '11/09 11:00',
    roomType: '标准大床房',
    rating: 4.5,
    nightlyPrice: 720,
    totalPrice: 2880,
    scheduleAt: '2026-11-05T15:00:00',
    scheduleLabel: '11/05 · 15:00',
    location: '首尔市中区',
    tags: ['购物方便', '热门区域'],
    description: '更适合喜欢市中心商圈、购物与餐饮的行程搭配。',
  },
  {
    id: 'hotel-3',
    type: 'hotel',
    title: '首尔花园酒店',
    subtitle: '首尔 · 麻浦',
    district: '麻浦区',
    checkIn: '11/05 15:00',
    checkOut: '11/09 11:00',
    roomType: '高级房',
    rating: 4.4,
    nightlyPrice: 610,
    totalPrice: 2440,
    scheduleAt: '2026-11-05T15:00:00',
    scheduleLabel: '11/05 · 15:00',
    location: '首尔市麻浦区',
    tags: ['预算友好', '安静'],
    description: '总价更低，适合成本更敏感的方案。',
  },
];

const spots: SpotOption[] = [
  {
    id: 'spot-1',
    type: 'spot',
    title: '景福宫 + 北村韩屋村',
    subtitle: '城市观光',
    area: '钟路区',
    openHours: '09:00 - 18:00',
    recommendedDuration: '半天',
    ticketPrice: 60,
    scheduleAt: '2026-11-06T10:00:00',
    scheduleLabel: '11/06 · 10:00',
    location: '首尔钟路区',
    tags: ['文化体验', '经典景点'],
    description: '适合首次到首尔的经典路线，可作为白天行程。',
  },
  {
    id: 'spot-2',
    type: 'spot',
    title: 'Seoul Music Week',
    subtitle: '音乐活动',
    area: '弘大',
    openHours: '19:00 - 22:30',
    recommendedDuration: '半天',
    ticketPrice: 320,
    scheduleAt: '2026-11-06T19:00:00',
    scheduleLabel: '11/06 · 19:00',
    location: '首尔弘大 Live House',
    tags: ['演唱会', '高度匹配', '推荐'],
    description: '与你的“现场音乐 / 演唱会”偏好高度匹配，是当前最推荐的活动。',
  },
  {
    id: 'spot-3',
    type: 'spot',
    title: '汉江夜游',
    subtitle: '城市夜景',
    area: '汝矣岛',
    openHours: '18:00 - 22:00',
    recommendedDuration: '2-3 小时',
    ticketPrice: 120,
    scheduleAt: '2026-11-07T18:30:00',
    scheduleLabel: '11/07 · 18:30',
    location: '首尔汝矣岛',
    tags: ['夜景', '轻松行程'],
    description: '节奏更轻松，适合行程后半段放松。',
  },
];

const allOptions: OptionItem[] = [...flights, ...hotels, ...spots];

const formatPrice = (price: number) => `HKD ${price.toLocaleString()}`;
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
        '你好，我已经准备了一组可选择的机票、酒店和景点。你可以在右侧查看详情，并把喜欢的项目添加到“旅行计划”中。',
    },
  ]);

  const [draft, setDraft] = useState('');
  const [activeTab, setActiveTab] = useState<OptionType>('flight');
  const [planItemIds, setPlanItemIds] = useState<string[]>([]);
  const [detailItem, setDetailItem] = useState<OptionItem | null>(null);
  const [routePlan, setRoutePlan] = useState<RoutePlan | null>(null);
  const [activeStyle, setActiveStyle] = useState('');
  const [activeDay, setActiveDay] = useState<number | 'all'>('all');
  const [planning, setPlanning] = useState(false);
  const [mapPosition, setMapPosition] = useState(() => {
    if (typeof window === 'undefined') return { x: 760, y: 520 };

    const defaultWidth = Math.min(360, window.innerWidth - 32);
    const defaultHeight = Math.min(318, window.innerHeight - 96);

    return {
      x: Math.max(12, window.innerWidth - defaultWidth - 24),
      y: Math.max(68, window.innerHeight - defaultHeight - 10),
    };
  });
  const [isMapExpanded, setIsMapExpanded] = useState(false);
  const mapDragRef = useRef<{ offsetX: number; offsetY: number } | null>(null);

  const styleBlocks = useMemo(
    () => routePlan?.blocks.filter((b) => b.plan_style === activeStyle) ?? [],
    [routePlan, activeStyle],
  );
  const styleLegs = useMemo(
    () => routePlan?.legs.filter((leg) => leg.plan_style === activeStyle) ?? [],
    [routePlan, activeStyle],
  );
  const planDays = useMemo(
    () => Array.from(new Set(styleBlocks.map((b) => b.day))).sort((a, b) => a - b),
    [styleBlocks],
  );

  const visibleOptions: OptionItem[] = useMemo(() => {
    if (activeTab === 'flight') return flights;
    if (activeTab === 'hotel') return hotels;
    return spots;
  }, [activeTab]);

  const travelPlan = useMemo(
    () =>
      allOptions
        .filter((item) => planItemIds.includes(item.id))
        .sort(
          (a, b) =>
            new Date(a.scheduleAt).getTime() - new Date(b.scheduleAt).getTime(),
        ),
    [planItemIds],
  );

  const totalBudget = useMemo(
    () => travelPlan.reduce((sum, item) => sum + getOptionPrice(item), 0),
    [travelPlan],
  );

  function addAssistantMessage(content: string) {
    setMessages((prev) => [...prev, { id: buildId(), role: 'assistant', content }]);
  }

  async function requestPlan(query: string) {
    setPlanning(true);
    try {
      const raw = await api.plan({ query });
      if (typeof raw.error === 'string') {
        addAssistantMessage(`规划失败：${raw.error}`);
        return;
      }
      const plan = (raw.plan ?? {}) as {
        destination?: string;
        plans?: Array<{ style?: string }>;
        blocks?: RouteBlock[];
        legs?: RouteLeg[];
        error?: string;
      };
      if (plan.error) {
        addAssistantMessage(`规划失败：${plan.error}`);
        return;
      }
      const styles = (plan.plans ?? []).map((p) => p.style ?? '').filter(Boolean);
      const blocks = plan.blocks ?? [];
      const legs = plan.legs ?? [];
      setRoutePlan({ destination: plan.destination ?? '', styles, blocks, legs });
      setActiveStyle(styles[0] ?? '');
      setActiveDay('all');
      const located = blocks.filter((b) => b.lng != null).length;
      addAssistantMessage(
        legs.length > 0
          ? `已为「${plan.destination}」生成 ${styles.length} 个方案，右上地图展示了 ${located} 个地点和 ${legs.length} 段真实路线。`
          : `已为「${plan.destination}」生成 ${styles.length} 个方案，但没有拿到地图路线（检查 PlanAgent 的 AMAP_KEY）。`,
      );
    } catch (err) {
      addAssistantMessage(`规划失败：${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPlanning(false);
    }
  }

  function handleSend() {
    const content = draft.trim();
    if (!content || planning) return;

    setMessages((prev) => [
      ...prev,
      {
        id: buildId(),
        role: 'user',
        content,
      },
      {
        id: buildId(),
        role: 'assistant',
        content: '正在搜索并规划行程，通常需要 1~2 分钟…',
      },
    ]);

    setDraft('');
    void requestPlan(content);
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

  function handleMapDragStart(event: ReactPointerEvent<HTMLDivElement>) {
    const card = event.currentTarget.parentElement;
    if (!card) return;

    event.currentTarget.setPointerCapture(event.pointerId);
    const rect = card.getBoundingClientRect();
    mapDragRef.current = {
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
    };
  }

  function handleMapDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = mapDragRef.current;
    const card = event.currentTarget.parentElement;
    if (!drag || !card || typeof window === 'undefined') return;

    const rect = card.getBoundingClientRect();
    const minX = 12;
    const minY = 68;
    const maxX = Math.max(minX, window.innerWidth - rect.width - 12);
    const maxY = Math.max(minY, window.innerHeight - rect.height - 12);

    setMapPosition({
      x: Math.min(maxX, Math.max(minX, event.clientX - drag.offsetX)),
      y: Math.min(maxY, Math.max(minY, event.clientY - drag.offsetY)),
    });
  }

  function handleMapDragEnd(event: ReactPointerEvent<HTMLDivElement>) {
    mapDragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  function toggleMapExpanded() {
    const nextExpanded = !isMapExpanded;
    setIsMapExpanded(nextExpanded);

    if (typeof window === 'undefined') return;

    const width = nextExpanded
      ? Math.min(900, window.innerWidth - 80)
      : Math.min(360, window.innerWidth - 32);
    const height = nextExpanded
      ? Math.min(650, window.innerHeight - 92)
      : Math.min(318, window.innerHeight - 96);

    setMapPosition({
      x: Math.max(12, Math.round((window.innerWidth - width) / 2)),
      y: Math.max(68, Math.round((window.innerHeight - height) / 2)),
    });
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
              disabled={!draft.trim() || planning}
            >
              发送
            </button>
          </div>
        </aside>

        {/* 中间：旅行计划 */}
        <section className="ta-plan-card ta-plan-column">
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
              <div>从右侧待选行程中添加机票、酒店或景点。</div>
              <p className="ta-plan-helper">
                在左侧描述你的行程，例如「宁波 10月1日到10月3日」
              </p>
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

        {/* 右侧：可供选择的行程 */}
        <section className="ta-options-panel ta-options-column">
          <div className="ta-options-header">
            <div>
              <span className="ta-section-kicker">OPTIONS</span>
              <h2>待选行程</h2>
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
      </div>

      {/* 悬浮地图：拖动标题栏可以自由移动 */}
      <section
        className={`ta-floating-map ${isMapExpanded ? 'expanded' : ''}`}
        style={{ left: mapPosition.x, top: mapPosition.y }}
        aria-label="可移动地图"
      >
        <div
          className="ta-floating-map-handle"
          onPointerDown={handleMapDragStart}
          onPointerMove={handleMapDrag}
          onPointerUp={handleMapDragEnd}
          onPointerCancel={handleMapDragEnd}
        >
          <div>
            <span className="ta-section-kicker">MAP</span>
            <strong>{routePlan?.destination ? `${routePlan.destination} 路线` : '地图'}</strong>
          </div>
          <div className="ta-floating-map-actions">
            <span className="ta-map-drag-hint">拖动移动</span>
            <button
              type="button"
              className="ta-map-expand-button"
              onPointerDown={(event) => event.stopPropagation()}
              onClick={(event) => {
                event.stopPropagation();
                toggleMapExpanded();
              }}
              aria-label={isMapExpanded ? '缩小地图' : '展开地图'}
            >
              {isMapExpanded ? '缩小' : '展开'}
            </button>
          </div>
        </div>

        {routePlan && routePlan.styles.length > 1 && (
          <div className="ta-floating-map-style-tabs ta-tabs">
            {routePlan.styles.map((style) => (
              <button
                key={style}
                type="button"
                className={style === activeStyle ? 'active' : ''}
                onClick={() => {
                  setActiveStyle(style);
                  setActiveDay('all');
                }}
              >
                {style}
              </button>
            ))}
          </div>
        )}

        {routePlan ? (
          <>
            <div className="ta-tabs ta-map-days ta-floating-map-days">
              <button
                type="button"
                className={activeDay === 'all' ? 'active' : ''}
                onClick={() => setActiveDay('all')}
              >
                全部
              </button>
              {planDays.map((day) => (
                <button
                  key={day}
                  type="button"
                  className={activeDay === day ? 'active' : ''}
                  onClick={() => setActiveDay(day)}
                >
                  D{day}
                </button>
              ))}
            </div>
            <TripMap blocks={styleBlocks} legs={styleLegs} day={activeDay} />
          </>
        ) : (
          <div className="ta-map-placeholder ta-floating-map-placeholder">
            <span>{planning ? '正在规划路线…' : '地图区域'}</span>
            {planning && <p>生成后会在这里显示每天的路线</p>}
          </div>
        )}
      </section>

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
