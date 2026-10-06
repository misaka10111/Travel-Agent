import { useMemo, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent as ReactPointerEvent } from 'react';
import { api } from '../api/client';
import { TripMap } from '../components/TripMap';
import type { RouteBlock, RouteLeg } from '../components/TripMap';
import { usePlanStream } from '../hooks/usePlanStream';

type ChatMessage = {
  id: string;
  role: 'assistant' | 'user';
  content: string;
};

type OptionType = 'flight' | 'hotel' | 'spot' | 'event' | 'food';

type BaseOption = {
  id: string;
  title: string;
  subtitle: string;
  scheduleAt: string;
  scheduleLabel: string;
  location: string;
  tags: string[];
  description: string;
  url?: string;
  image?: string;
  lng?: number;
  lat?: number;
};

type FlightOption = BaseOption & {
  type: 'flight';
  mode: 'flight' | 'train';
  from: string;
  to: string;
  departTime: string;
  arriveTime: string;
  carrier: string;
  code: string;
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

type EventOption = BaseOption & {
  type: 'event';
  price: number;
};

type FoodOption = BaseOption & {
  type: 'food';
  cuisine: string;
  rating: number;
  pricePerPerson: number;
  businessArea: string;
  address: string;
  detailUrl: string;
  mapUrl: string;
};

type OptionItem = FlightOption | HotelOption | SpotOption | EventOption | FoodOption;

const FIXED_QUESTIONS = [
  { key: 'destination', field: 'destination', question: '您想去哪里？', options: [], kind: 'text' },
  { key: 'start_date', field: 'start_date', question: '出发日期是哪天？', options: [], kind: 'date' },
  { key: 'end_date', field: 'end_date', question: '返程日期是哪天？', options: [], kind: 'date' },
  { key: 'origin', field: 'origin', question: '您从哪里出发？', options: [], kind: 'text' },
  { key: 'travelers', field: 'travelers', question: '出行人数是？', options: ['1人', '2人', '3人', '4人', '5人以上'], kind: 'options' },
  { key: 'budget_tiers', field: 'budget_tiers', question: '预算档位是？', options: ['经济', '舒适', '豪华', '不设限'], kind: 'options' },
  { key: 'total_budget', field: 'total_budget', question: '本次旅行总预算大概多少元？', options: [], kind: 'text' },
  { key: 'purposes', field: 'purposes', question: '这次旅行的主要目的是？', options: ['自然风光', '深度文化', '美食之旅', '亲子', '购物', '摄影', '冒险户外'], kind: 'options' },
] as const;

function parsePrice(value: unknown): number {
  const n = parseFloat(String(value ?? '').replace(/[^0-9.]/g, ''));
  return Number.isFinite(n) ? n : 0;
}

type RoutePlan = {
  destination: string;
  start_date: string;
  end_date: string;
  total_cost?: number;
  budget_status?: string;
  styles: string[];
  summaries: Record<string, string>;
  blocks: RouteBlock[];
  legs: RouteLeg[];
};

function mapFlights(items: unknown): FlightOption[] {
  const list = Array.isArray(items)
    ? items
    : [
        ...(((items as { outbound?: unknown[] })?.outbound) ?? []),
        ...(((items as { inbound?: unknown[] })?.inbound) ?? []),
      ];
  return list.slice(0, 10).map((f: any, i: number) => ({
    id: `flight-${i}`,
    type: 'flight',
    mode: 'flight',
    title: `${f?.airline ?? ''}${f?.flight_no ?? ''}`,
    subtitle: `${f?.dep_station ?? ''} → ${f?.arr_station ?? ''}`,
    from: f?.dep_station ?? '',
    to: f?.arr_station ?? '',
    departTime: f?.dep_time ?? '',
    arriveTime: f?.arr_time ?? '',
    carrier: f?.airline ?? '',
    code: f?.flight_no ?? '',
    duration: f?.duration ?? '',
    price: parsePrice(f?.price),
    scheduleAt: f?.dep_time ?? '',
    scheduleLabel: f?.dep_time ?? '',
    location: f?.arr_station ?? '',
    tags: f?.seat ? [f.seat] : [],
    description: `${f?.airline ?? ''}${f?.flight_no ?? ''}，${f?.dep_station ?? ''} → ${f?.arr_station ?? ''}`,
    url: f?.url ?? '',
  }));
}

function mapTrains(items: unknown): FlightOption[] {
  const list = Array.isArray(items)
    ? items
    : [
        ...(((items as { outbound?: unknown[] })?.outbound) ?? []),
        ...(((items as { inbound?: unknown[] })?.inbound) ?? []),
      ];
  return list.slice(0, 10).map((t: any, i: number) => ({
    id: `train-${i}`,
    type: 'flight',
    mode: 'train',
    title: `${t?.transport ?? ''}${t?.train_no ?? ''}`,
    subtitle: `${t?.dep_station ?? ''} → ${t?.arr_station ?? ''}`,
    from: t?.dep_station ?? '',
    to: t?.arr_station ?? '',
    departTime: t?.dep_time ?? '',
    arriveTime: t?.arr_time ?? '',
    carrier: t?.transport ?? '',
    code: t?.train_no ?? '',
    duration: t?.duration ?? '',
    price: parsePrice(t?.price),
    scheduleAt: t?.dep_time ?? '',
    scheduleLabel: t?.dep_time ?? '',
    location: t?.arr_station ?? '',
    tags: t?.seat ? [t.seat] : [],
    description: `${t?.transport ?? ''}${t?.train_no ?? ''}，${t?.dep_station ?? ''} → ${t?.arr_station ?? ''}`,
    url: t?.url ?? '',
  }));
}

function mapHotels(items: unknown): HotelOption[] {
  return ((items as unknown[]) || []).slice(0, 30).map((h: any, i: number) => {
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
    url: h?.url ?? '',
    image: h?.image ?? '',
    lng: h?.longitude != null ? Number(h.longitude) : undefined,
    lat: h?.latitude != null ? Number(h.latitude) : undefined,
  };
  });
}

function mapSpots(items: unknown): SpotOption[] {
  return ((items as unknown[]) || []).slice(0, 50).map((p: any, i: number) => ({
    id: `spot-${i}`,
    type: 'spot',
    title: p?.name ?? '',
    subtitle: p?.category ?? '',
    area: p?.district_label ?? p?.category ?? '',
    openHours: '',
    recommendedDuration: '',
    ticketPrice: 0,
    scheduleAt: '',
    scheduleLabel: '',
    location: '',
    tags: p?.rank ? [p.rank] : [],
    description: p?.description ?? '',
    url: p?.url ?? '',
    image: p?.image ?? '',
    lng: p?.longitude != null ? Number(p.longitude) : undefined,
    lat: p?.latitude != null ? Number(p.latitude) : undefined,
  }));
}

function mapEvents(items: unknown): EventOption[] {
  return ((items as unknown[]) || []).slice(0, 20).map((e: any, i: number) => ({
    id: `event-${i}`,
    type: 'event',
    title: e?.title ?? '',
    subtitle: e?.content ?? '',
    scheduleAt: '',
    scheduleLabel: '',
    location: '',
    tags: String(e?.content ?? '').split(',').map((t) => t.trim()).filter(Boolean),
    description: e?.content ?? '',
    price: 0,
    url: e?.url ?? '',
  }));
}

function mapFood(items: unknown): FoodOption[] {
  return ((items as unknown[]) || []).slice(0, 50).map((f: any, i: number) => ({
    id: `food-${i}`,
    type: 'food',
    title: f?.name ?? f?.title ?? '',
    subtitle: f?.cuisine ?? f?.business_area ?? '',
    cuisine: f?.cuisine ?? '',
    rating: parseFloat(String(f?.rating ?? 0)) || 0,
    pricePerPerson: parsePrice(f?.price_per_person ?? f?.price),
    businessArea: f?.business_area ?? '',
    address: f?.address ?? '',
    detailUrl: f?.poi_detail_url ?? f?.url ?? '',
    mapUrl: f?.map_url ?? '',
    url: f?.poi_detail_url ?? f?.map_url ?? f?.url ?? '',
    lng: f?.longitude != null ? Number(f.longitude) : undefined,
    lat: f?.latitude != null ? Number(f.latitude) : undefined,
    scheduleAt: '',
    scheduleLabel: '',
    location: f?.business_area ?? f?.address ?? '',
    tags: [f?.cuisine, f?.business_area, f?.rating ? `${f.rating}分` : ''].filter(Boolean),
    description: f?.address ?? f?.content ?? '',
  }));
}

const formatPrice = (price: number) => `¥ ${price.toLocaleString()}`;
const buildId = () => `${Date.now()}-${Math.random()}`;

function timeToMinutes(value: string) {
  const match = value.match(/(\d{1,2}):(\d{2})/);
  return match ? Number(match[1]) * 60 + Number(match[2]) : null;
}

function rangeToMinutes(value: string): [number, number] | null {
  const match = value.match(/(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})/);
  if (!match) {
    const start = timeToMinutes(value);
    return start == null ? null : [start, start + 60];
  }
  return [
    Number(match[1]) * 60 + Number(match[2]),
    Number(match[3]) * 60 + Number(match[4]),
  ];
}

function minutesToRange(start: number) {
  const h = Math.floor(start / 60);
  const m = start % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}-${String(
    Math.floor((start + 60) / 60),
  ).padStart(2, '0')}:${String((start + 60) % 60).padStart(2, '0')}`;
}

function coordDistance(
  a: { lng: number; lat: number },
  b: { lng: number; lat: number },
) {
  const dx = (b.lng - a.lng) * Math.cos(((a.lat + b.lat) / 2) * (Math.PI / 180));
  const dy = b.lat - a.lat;
  return Math.hypot(dx, dy);
}

function getOptionPrice(item: OptionItem) {
  if (item.type === 'flight') return item.price;
  if (item.type === 'hotel') return item.totalPrice;
  if (item.type === 'event') return item.price;
  if (item.type === 'food') return item.pricePerPerson;
  return item.ticketPrice;
}

function getOptionIcon(item: OptionItem) {
  if (item.type === 'flight') return item.mode === 'train' ? '🚄' : '✈';
  if (item.type === 'hotel') return '▣';
  if (item.type === 'event') return '♪';
  if (item.type === 'food') return '食';
  return '●';
}

function getOptionTypeLabel(type: OptionType) {
  if (type === 'flight') return '出行';
  if (type === 'hotel') return '酒店';
  if (type === 'event') return '活动';
  if (type === 'food') return '美食';
  return '景点';
}

const AIRLINE_ALIAS: Record<string, string> = {
  春秋航空: '春秋',
  东方航空: '东航',
  中国国航: '国航',
  中国南方航空: '南航',
  海南航空: '海航',
  四川航空: '川航',
  吉祥航空: '吉祥',
  厦门航空: '厦航',
  深圳航空: '深航',
};

function getCarrierBadge(item: OptionItem) {
  if (item.type !== 'flight') return '';
  if (item.mode === 'train') return '高铁';
  return AIRLINE_ALIAS[item.carrier] ?? (item.carrier.slice(0, 2) || '航班');
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
  const [batchIndex, setBatchIndex] = useState<Record<string, number>>({});
  const [socialBatch, setSocialBatch] = useState(0);
  const [planItemIds, setPlanItemIds] = useState<string[]>([]);
  const [detailItem, setDetailItem] = useState<OptionItem | null>(null);
  const [options, setOptions] = useState<OptionItem[]>([]);
  const [socialFood, setSocialFood] = useState<
    Array<{ platform: string; title: string; url: string }>
  >([]);
  const [pendingAdd, setPendingAdd] = useState<OptionItem | null>(null);
  const [collectStep, setCollectStep] = useState<number | null>(null);
  const [collectData, setCollectData] = useState<Record<string, unknown>>({});
  const [pendingConfirm, setPendingConfirm] = useState<{
    summary: string;
    mode: string;
    targets?: unknown[];
    instruction: string;
  } | null>(null);
  const [routePlan, setRoutePlan] = useState<RoutePlan | null>(null);
  const [activeStyle, setActiveStyle] = useState('');
  const [activeDay, setActiveDay] = useState<number | 'all'>('all');
  const [expandedStyle, setExpandedStyle] = useState<string | null>(null);
  const [confirmedStyle, setConfirmedStyle] = useState<string | null>(null);
  const [planRating, setPlanRating] = useState<number | null>(null);
  const [planFeedback, setPlanFeedback] = useState('');
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [clarify, setClarify] = useState<{
    destination: string;
    start_date: string;
    end_date: string;
  } | null>(null);
  const [originInput, setOriginInput] = useState('');
  const [question, setQuestion] = useState<{
    field: string;
    question: string;
    options: string[];
  } | null>(null);
  const [answer, setAnswer] = useState('');
  const [selectedOptions, setSelectedOptions] = useState<string[]>([]);
  const [weatherData, setWeatherData] = useState<{ days?: Array<Record<string, unknown>> } | null>(null);
  const [selectedBlocks, setSelectedBlocks] = useState<Set<string>>(new Set());
  const [selectedFoodPoints, setSelectedFoodPoints] = useState<RouteBlock[]>([]);
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
  const [isMapHidden, setIsMapHidden] = useState(false);
  const mapDragRef = useRef<{ offsetX: number; offsetY: number } | null>(null);
  const { start: startPlanStream, stop: stopPlanStream } = usePlanStream();

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
    if (activeTab === 'flight') return options.filter((o) => o.type === 'flight');
    if (activeTab === 'hotel') {
      const list = options.filter((o) => o.type === 'hotel');
      const start = ((batchIndex[activeTab] ?? 0) * 5) % Math.max(1, list.length);
      return list.slice(start, start + 5);
    }
    if (activeTab === 'spot') {
      const list = options.filter((o) => o.type === 'spot');
      const start = ((batchIndex[activeTab] ?? 0) * 5) % Math.max(1, list.length);
      return list.slice(start, start + 5);
    }
    if (activeTab === 'event') return options.filter((o) => o.type === 'event');
    {
      const list = options.filter((o) => o.type === 'food');
      const start = ((batchIndex[activeTab] ?? 0) * 5) % Math.max(1, list.length);
      return list.slice(start, start + 5);
    }
  }, [activeTab, options, batchIndex]);

  const visibleSocialFood = useMemo(() => {
    if (activeTab !== 'food') return [];
    const take = (platform: string) => {
      const list = socialFood.filter((item) => item.platform === platform);
      const start = (socialBatch * 2) % Math.max(1, list.length);
      return list.slice(start, start + 2);
    };
    return [...take('抖音'), ...take('小红书')];
  }, [activeTab, socialFood, socialBatch]);

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

  function addAssistantMessage(content: string) {
    setMessages((prev) => [...prev, { id: buildId(), role: 'assistant', content }]);
  }

  function updateLastAssistantMessage(content: string) {
    setMessages((prev) => {
      const last = prev[prev.length - 1];
      if (!last || last.role !== 'assistant') return prev;
      return [...prev.slice(0, -1), { ...last, content }];
    });
  }

  function applySearchData(searchData: Record<string, unknown>) {
    setBatchIndex({});
    setSocialBatch(0);
    setWeatherData(
      (searchData.weather as { days?: Array<Record<string, unknown>> } | undefined) ?? null,
    );
    const flights = mapFlights(searchData.flights);
    const trains = mapTrains(searchData.trains);
    const hotels = mapHotels(searchData.hotels);
    const spots = mapSpots(searchData.poi);
    const events = mapEvents(searchData.events);
    const food = mapFood(searchData.food);
    setSocialFood(
      (searchData.social_food as Array<{ platform: string; title: string; url: string }>) ?? [],
    );
    setOptions([...flights, ...trains, ...hotels, ...spots, ...events, ...food]);
    if (flights.length + trains.length === 0) {
      if (hotels.length > 0) setActiveTab('hotel');
      else if (spots.length > 0) setActiveTab('spot');
      else if (events.length > 0) setActiveTab('event');
      else if (food.length > 0) setActiveTab('food');
    }
  }

  async function runPlan(payload: Record<string, unknown>) {
    setPlanning(true);
    await startPlanStream(
      payload,
      (event) => {
        if (event.type === 'node') {
          const nodeData = event.data as { node?: unknown; search?: unknown };
          const node = String(nodeData.node ?? '');
          if (node === 'search' && nodeData.search) {
            applySearchData(nodeData.search as Record<string, unknown>);
          }
          const status =
            node === 'search'
              ? '已找到机票、酒店、景点、活动等信息，正在生成方案…'
              : node === 'prepare_memory'
                ? '正在读取你的偏好和历史行程…'
              : node === 'plan'
                ? '正在生成两个旅行方案…'
                : node === 'validate'
                  ? '正在审核方案…'
                  : '正在处理…';
          updateLastAssistantMessage(status);
          return;
        }

        if (event.type === 'clarify') {
          setClarify({
            destination: String(event.data.destination ?? ''),
            start_date: String(event.data.start_date ?? ''),
            end_date: String(event.data.end_date ?? ''),
          });
          updateLastAssistantMessage('还差一点信息，请补充出发地～');
          return;
        }

        if (event.type === 'error') {
          updateLastAssistantMessage(`规划失败：${event.error}`);
          return;
        }

        const raw = event.data ?? {};
        const plan = (raw.plan ?? {}) as {
          destination?: string;
          start_date?: string;
          end_date?: string;
          plans?: Array<{ style?: string; summary?: string }>;
          blocks?: RouteBlock[];
          legs?: RouteLeg[];
          total_cost?: number;
          budget_status?: string;
          error?: string;
        };
        if (plan.error) {
          updateLastAssistantMessage(`规划失败：${plan.error}`);
          return;
        }

        const styles = (plan.plans ?? []).map((p) => p.style ?? '').filter(Boolean);
        const summaries: Record<string, string> = {};
        for (const p of plan.plans ?? []) {
          if (p.style) summaries[p.style] = p.summary ?? '';
        }
        const blocks = plan.blocks ?? [];
        const legs = plan.legs ?? [];
        setRoutePlan({
          destination: plan.destination ?? '',
          start_date: plan.start_date ?? '',
          end_date: plan.end_date ?? '',
          styles,
          summaries,
          blocks,
          legs,
          total_cost: plan.total_cost,
          budget_status: plan.budget_status,
        });
        setActiveStyle(styles[0] ?? '');
        setActiveDay('all');
        setExpandedStyle(null);
        setConfirmedStyle(null);
        setPlanRating(null);
        setPlanFeedback('');
        setSaveState('idle');
        setSelectedBlocks(new Set());

        applySearchData((raw.search ?? {}) as Record<string, unknown>);
        setPlanItemIds([]);
        setDetailItem(null);

        const located = blocks.filter((b) => b.lng != null).length;
        updateLastAssistantMessage(
          legs.length > 0
            ? `已为「${plan.destination}」生成 ${styles.length} 个方案，右上地图展示了 ${located} 个地点和 ${legs.length} 段真实路线。`
            : `已为「${plan.destination}」生成 ${styles.length} 个方案，但没有拿到地图路线（检查 PlanAgent 的 AMAP_KEY）。`,
        );
      },
    );

    setPlanning(false);
  }

  async function requestPlan(query: string) {
    const userId = localStorage.getItem('currentUser') || '';
    let profile: unknown = null;
    let basic: unknown = null;
    try {
      profile = JSON.parse(localStorage.getItem('userProfile') || 'null');
    } catch {
      profile = null;
    }
    try {
      basic = JSON.parse(localStorage.getItem('tripInfo') || 'null');
    } catch {
      basic = null;
    }
    await runPlan({
      query,
      ...(userId ? { user_id: userId } : {}),
      ...(profile ? { profile } : {}),
      ...(basic ? { basic } : {}),
    });
  }

  async function modifyPlan(
    instruction: string,
    mode?: string,
    targets?: unknown[],
  ) {
    setPlanning(true);
    try {
      if (!routePlan) return;
      const userId = localStorage.getItem('currentUser') || '';
      let profile: unknown = null;
      let basic: unknown = null;
      try {
        profile = JSON.parse(localStorage.getItem('userProfile') || 'null');
      } catch {
        profile = null;
      }
      try {
        basic = JSON.parse(localStorage.getItem('tripInfo') || 'null');
      } catch {
        basic = null;
      }
      const blocks = routePlan.blocks;
      const raw = await api.plan({
        destination: routePlan.destination,
        start_date: routePlan.start_date,
        end_date: routePlan.end_date,
        ...(userId ? { user_id: userId } : {}),
        ...(profile ? { profile } : {}),
        ...(basic ? { basic } : {}),
        modify: {
          blocks,
          instruction,
          ...(mode ? { mode } : {}),
          ...(targets ? { targets } : {}),
        },
      });
      if (typeof raw.error === 'string') {
        updateLastAssistantMessage(`修改失败：${raw.error}`);
        return;
      }
      const originalById = new Map(routePlan.blocks.map((block) => [block.id, block]));
      const newBlocks = ((raw.blocks ?? routePlan.blocks) as RouteBlock[]).map(
        (block) => {
          const original = originalById.get(block.id);
          return {
            ...block,
            plan_style: block.plan_style ?? original?.plan_style,
            lng: block.lng ?? original?.lng,
            lat: block.lat ?? original?.lat,
          };
        },
      );
      setRoutePlan((prev) => (prev ? { ...prev, blocks: newBlocks, legs: [] } : prev));
      setSelectedBlocks(new Set());
      updateLastAssistantMessage('已根据你的意见修改计划。');
    } catch (error) {
      updateLastAssistantMessage(
        `修改失败：${error instanceof Error ? error.message : String(error)}`,
      );
    } finally {
      setPlanning(false);
    }
  }

  async function resolveIntent(history: ChatMessage[], content: string) {
    setPlanning(true);
    try {
      const result = await api.question({
        messages: history.map((m) => ({ role: m.role, content: m.content })),
        has_plan: !!routePlan,
      });
      const action = String(result.action ?? '');
      if (action === 'explain') {
        const answer = String(result.answer ?? '');
        const links = Array.isArray(result.links)
          ? (result.links as Array<{ title?: string; url?: string }>)
          : [];
        const linkText = links
          .map((link) => `${link.title ?? ''}：${link.url ?? ''}`)
          .join('\n');
        addAssistantMessage(
          `${answer}${linkText ? `\n\n相关链接：\n${linkText}` : ''}`,
        );
        return;
      }
      if (action === 'ask') {
        setQuestion({
          field: String(result.field ?? ''),
          question: String(result.question ?? ''),
          options: Array.isArray(result.options)
            ? (result.options as string[])
            : [],
        });
        addAssistantMessage(String(result.question ?? ''));
        return;
      }
      if (action === 'plan') {
        const data = (result.data ?? {}) as Record<string, unknown>;
        setQuestion(null);
        addAssistantMessage('正在搜索并规划行程…');
        await runPlan({
          destination: data.destination,
          start_date: data.start_date,
          end_date: data.end_date,
          basic: {
            origin: data.origin,
            travelers: data.travelers,
            budget_tiers: data.budget_tiers,
            total_budget: data.total_budget,
            purposes: data.purposes,
            requested_pois: data.requested_pois,
          },
        });
        return;
      }
      if (action === 'confirm') {
        setPendingConfirm({
          summary: String(result.summary ?? ''),
          mode: String(result.mode ?? 'global'),
          targets: Array.isArray(result.targets)
            ? (result.targets as unknown[])
            : undefined,
          instruction: String(result.instruction ?? content),
        });
        addAssistantMessage(
          `${String(result.summary ?? '')}\n是否按此修改？`,
        );
        return;
      }
      if (action === 'modify') {
        setQuestion(null);
        addAssistantMessage('正在修改计划…');
        const selectedBlocksNow = selectedBlocks.size > 0 && routePlan
          ? routePlan.blocks.filter((block) => selectedBlocks.has(block.id))
          : [];
        const mode = selectedBlocksNow.length > 0
          ? 'block'
          : String(result.mode ?? '');
        const targets = selectedBlocksNow.length > 0
          ? selectedBlocksNow.map((block) => ({
              name: block.name,
              type: block.type,
              day: block.day,
            }))
          : Array.isArray(result.targets)
            ? (result.targets as unknown[])
            : undefined;
        await modifyPlan(
          String(result.instruction ?? content),
          mode,
          targets,
        );
        return;
      }
      addAssistantMessage('正在搜索并规划行程…');
      await requestPlan(content);
    } catch (error) {
      updateLastAssistantMessage(
        `处理失败：${error instanceof Error ? error.message : String(error)}`,
      );
    } finally {
      setPlanning(false);
    }
  }

  async function submitQuestion() {
    if (!question) return;
    const value =
      question.options.length > 0
        ? selectedOptions.join('、') || answer.trim()
        : answer.trim();
    if (!value) return;
    if (collectStep != null) {
      setMessages((prev) => [
        ...prev,
        { id: buildId(), role: 'user', content: value },
      ]);
      setQuestion(null);
      setAnswer('');
      handleCollectAnswer(value, [...selectedOptions]);
      setSelectedOptions([]);
      return;
    }
    const history: ChatMessage[] = [
      ...messages,
      { id: buildId(), role: 'user', content: value },
    ];
    setMessages((prev) => [
      ...prev,
      { id: buildId(), role: 'user', content: value },
    ]);
    setQuestion(null);
    setAnswer('');
    setSelectedOptions([]);
    await resolveIntent(history, value);
  }

  function askFixed(index: number) {
    const item = FIXED_QUESTIONS[index];
    if (!item) return;
    setQuestion({
      field: item.field,
      question: item.question,
      options: [...item.options],
    });
    setAnswer('');
    setSelectedOptions([]);
  }

  function startCollect() {
    setCollectStep(0);
    setCollectData({});
    askFixed(0);
  }

  async function confirmModification() {
    if (!pendingConfirm) return;
    const confirm = pendingConfirm;
    setPendingConfirm(null);
    addAssistantMessage('正在修改计划…');
    await modifyPlan(confirm.instruction, confirm.mode, confirm.targets);
  }

  function finishCollect(data: Record<string, unknown>) {
    setQuestion(null);
    setCollectStep(null);
    setCollectData({});
    addAssistantMessage('正在搜索并规划行程…');
    const userId = localStorage.getItem('currentUser') || '';
    let profile: unknown = null;
    try {
      profile = JSON.parse(localStorage.getItem('userProfile') || 'null');
    } catch {
      profile = null;
    }
    void runPlan({
      destination: data.destination,
      start_date: data.start_date,
      end_date: data.end_date,
      ...(userId ? { user_id: userId } : {}),
      ...(profile ? { profile } : {}),
      basic: {
        origin: data.origin,
        travelers: data.travelers,
        budget_tiers: data.budget_tiers,
        total_budget: data.total_budget,
        purposes: data.purposes,
      },
    });
  }

  function handleCollectAnswer(content: string, optionValues?: string[]) {
    if (collectStep == null) return;
    const item = FIXED_QUESTIONS[collectStep];
    const value = item.kind === 'options'
      ? (optionValues ?? selectedOptions)
      : content.trim();
    const nextData = { ...collectData, [item.key]: value };
    setCollectData(nextData);
    if (collectStep + 1 < FIXED_QUESTIONS.length) {
      setCollectStep(collectStep + 1);
      askFixed(collectStep + 1);
    } else {
      setCollectData(nextData);
      finishCollect(nextData);
    }
  }

  async function submitClarify() {
    if (!clarify || !originInput.trim()) return;
    let basic: Record<string, unknown> = {};
    try {
      basic = JSON.parse(localStorage.getItem('tripInfo') || '{}') || {};
    } catch {
      basic = {};
    }
    basic.origin = originInput.trim();
    localStorage.setItem('tripInfo', JSON.stringify(basic));

    const userId = localStorage.getItem('currentUser') || '';
    let profile: unknown = null;
    try {
      profile = JSON.parse(localStorage.getItem('userProfile') || 'null');
    } catch {
      profile = null;
    }
    const destination = clarify.destination;
    const start_date = clarify.start_date;
    const end_date = clarify.end_date;
    setClarify(null);
    setOriginInput('');
    await runPlan({
      destination,
      start_date,
      end_date,
      ...(userId ? { user_id: userId } : {}),
      ...(profile ? { profile } : {}),
      ...(basic ? { basic } : {}),
    });
  }

  function loadMockData() {
    const styles = ['轻享周末', '深度漫游'];
    const summaries: Record<string, string> = {
      轻享周末: '杭州 2 日轻松游，西湖、灵隐寺与河坊街，节奏舒缓、适合周末放松。',
      深度漫游: '杭州 2 日文化深度游，博物馆、古迹与老街区，安排更紧凑。',
    };
    const blocks: RouteBlock[] = [
      {
        id: 'mock-a-1',
        plan_style: '轻享周末',
        day: 1,
        date: '2026-10-02',
        type: '景点',
        time: '09:00-11:00',
        name: '西湖风景名胜区',
        note: '地铁1号线到龙翔桥，步行至断桥',
        lng: 120.1475,
        lat: 30.2444,
      },
      {
        id: 'mock-a-2',
        plan_style: '轻享周末',
        day: 1,
        date: '2026-10-02',
        type: '美食',
        time: '12:00-13:00',
        name: '楼外楼（孤山路店）',
        note: '西湖醋鱼、龙井虾仁',
        lng: 120.1324,
        lat: 30.2506,
      },
      {
        id: 'mock-a-3',
        plan_style: '轻享周末',
        day: 2,
        date: '2026-10-03',
        type: '景点',
        time: '10:00-12:00',
        name: '灵隐寺',
        note: '打车约 25 分钟',
        lng: 120.0996,
        lat: 30.2378,
      },
      {
        id: 'mock-a-4',
        plan_style: '轻享周末',
        day: 2,
        date: '2026-10-03',
        type: '酒店',
        time: '14:00',
        name: '杭州西子湖四季酒店',
        note: '湖景房，含双早',
        lng: 120.1512,
        lat: 30.2312,
      },
      {
        id: 'mock-b-1',
        plan_style: '深度漫游',
        day: 1,
        date: '2026-10-02',
        type: '景点',
        time: '09:30-11:30',
        name: '浙江省博物馆',
        note: '地铁2号线到武林门',
        lng: 120.1537,
        lat: 30.2666,
      },
      {
        id: 'mock-b-2',
        plan_style: '深度漫游',
        day: 1,
        date: '2026-10-02',
        type: '美食',
        time: '12:30-13:30',
        name: '知味观（仁和路店）',
        note: '小笼包、猫耳朵',
        lng: 120.1661,
        lat: 30.2461,
      },
      {
        id: 'mock-b-3',
        plan_style: '深度漫游',
        day: 2,
        date: '2026-10-03',
        type: '景点',
        time: '10:00-12:00',
        name: '河坊街与南宋御街',
        note: '地铁1号线到定安路',
        lng: 120.1706,
        lat: 30.2417,
      },
      {
        id: 'mock-b-4',
        plan_style: '深度漫游',
        day: 2,
        date: '2026-10-03',
        type: '酒店',
        time: '14:00',
        name: '杭州西湖国宾馆',
        note: '园林式酒店，安静',
        lng: 120.1213,
        lat: 30.2268,
      },
    ];
    const legs: RouteLeg[] = [
      {
        plan_style: '轻享周末',
        day: 1,
        from: 'mock-a-1',
        to: 'mock-a-2',
        mode: 'walk',
        distance_m: 1300,
        duration_s: 1080,
        polyline: [
          [120.1475, 30.2444],
          [120.1402, 30.2476],
          [120.1324, 30.2506],
        ],
      },
      {
        plan_style: '轻享周末',
        day: 2,
        from: 'mock-a-3',
        to: 'mock-a-4',
        mode: 'drive',
        distance_m: 6200,
        duration_s: 1380,
        polyline: [
          [120.0996, 30.2378],
          [120.1234, 30.2312],
          [120.1512, 30.2312],
        ],
      },
      {
        plan_style: '深度漫游',
        day: 1,
        from: 'mock-b-1',
        to: 'mock-b-2',
        mode: 'transit',
        distance_m: 2400,
        duration_s: 1500,
        lines: ['地铁1号线'],
        polyline: [
          [120.1537, 30.2666],
          [120.1598, 30.2563],
          [120.1661, 30.2461],
        ],
      },
    ];

    setRoutePlan({
      destination: '杭州',
      start_date: '2026-10-02',
      end_date: '2026-10-03',
      styles,
      summaries,
      blocks,
      legs,
    });
    setWeatherData({
      days: [
        {
          date: '2026-10-02',
          weather: '多云',
          temp_min: 20,
          temp_max: 27,
          humidity: 68,
        },
        {
          date: '2026-10-03',
          weather: '晴',
          temp_min: 21,
          temp_max: 29,
          humidity: 62,
        },
      ],
    });
    setOptions([
      {
        id: 'mock-flight-1',
        type: 'flight',
        mode: 'flight',
        title: 'MU5211',
        subtitle: '上海虹桥 → 杭州',
        from: '上海虹桥',
        to: '杭州',
        departTime: '08:30',
        arriveTime: '09:20',
        carrier: '东方航空',
        code: 'MU5211',
        duration: '50分钟',
        price: 420,
        scheduleAt: '08:30',
        scheduleLabel: '08:30',
        location: '杭州',
        tags: ['经济舱'],
        description: '东方航空 MU5211，上海虹桥 → 杭州。',
      },
      {
        id: 'mock-hotel-1',
        type: 'hotel',
        title: '杭州西子湖四季酒店',
        subtitle: '西湖区',
        district: '西湖区',
        checkIn: '2026-10-02',
        checkOut: '2026-10-03',
        roomType: '豪华湖景房',
        rating: 4.8,
        nightlyPrice: 1280,
        totalPrice: 1280,
        scheduleAt: '',
        scheduleLabel: '',
        location: '西湖区',
        tags: ['五星'],
        description: '杭州西子湖四季酒店，五星，西湖区。',
      },
      {
        id: 'mock-spot-1',
        type: 'spot',
        title: '西湖风景名胜区',
        subtitle: '自然风光',
        area: '西湖区',
        openHours: '全天',
        recommendedDuration: '3小时',
        ticketPrice: 0,
        scheduleAt: '',
        scheduleLabel: '',
        location: '西湖区',
        tags: ['5A'],
        description: '杭州经典自然风光，免费开放。',
      },
      {
        id: 'mock-event-1',
        type: 'event',
        title: '西湖音乐节',
        subtitle: '现场演出',
        price: 188,
        scheduleAt: '2026-10-02',
        scheduleLabel: '10月2日',
        location: '西湖',
        tags: ['音乐节'],
        description: '西湖音乐节，现场演出。',
      },
      {
        id: 'mock-food-1',
        type: 'food',
        title: '楼外楼（孤山路店）',
        subtitle: '杭帮菜',
        cuisine: '杭帮菜',
        rating: 4.6,
        pricePerPerson: 120,
        businessArea: '西湖',
        address: '杭州市西湖区孤山路30号',
        detailUrl: '',
        mapUrl: '',
        scheduleAt: '',
        scheduleLabel: '',
        location: '西湖',
        tags: ['杭帮菜', '4.6分'],
        description: '杭州市西湖区孤山路30号',
      },
    ]);
    setActiveStyle(styles[0] ?? '');
    setActiveDay('all');
    setExpandedStyle(null);
    setConfirmedStyle(null);
    setPlanRating(null);
    setPlanFeedback('');
    setSaveState('idle');
    setSelectedBlocks(new Set());
    addAssistantMessage('已加载前端测试数据，可在右侧「方案」中查看两个示例计划。');
  }

  function handleSend() {
    const content = draft.trim();
    if (!content || planning) return;

    if (pendingAdd) {
      setMessages((prev) => [
        ...prev,
        { id: buildId(), role: 'user', content },
      ]);
      setDraft('');
      if (/不加|取消|算了|不要/.test(content)) {
        setPendingAdd(null);
        addAssistantMessage('好的，已取消添加。');
        return;
      }
      handleAddReply(content);
      return;
    }

    setMessages((prev) => [
      ...prev,
      { id: buildId(), role: 'user', content },
    ]);
    setDraft('');

    if (routePlan) {
      const history: ChatMessage[] = [
        ...messages,
        { id: buildId(), role: 'user', content },
      ];
      void resolveIntent(history, content);
      return;
    }

    if (collectStep != null) {
      handleCollectAnswer(content);
      return;
    }

    startCollect();
    return;
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
    const userId = localStorage.getItem('currentUser') || '';
    if (userId) {
      void api.reportBehavior(userId, 'add', item.title, getOptionTypeLabel(item.type));
    }
  }

  function removeFromPlan(itemId: string) {
    const item = options.find((o) => o.id === itemId);
    setPlanItemIds((current) => current.filter((id) => id !== itemId));
    const userId = localStorage.getItem('currentUser') || '';
    if (userId && item) {
      void api.reportBehavior(userId, 'remove', item.title, getOptionTypeLabel(item.type));
    }
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

  function optionToBlockType(item: OptionItem) {
    if (item.type === 'flight') return '交通';
    if (item.type === 'hotel') return '酒店';
    if (item.type === 'spot') return '景点';
    if (item.type === 'event') return '活动';
    return '美食';
  }

  function startAdd(item: OptionItem) {
    setPendingAdd(item);
    addAssistantMessage(
      `要把「${item.title}」加到第几天？请回复例如“第2天 14:00”`,
    );
  }

  function handleAddReply(content: string) {
    if (!pendingAdd || !routePlan) return;
    const dayMatch = content.match(/第?\s*(\d+|[一二三四五六七八九十]+)\s*天/);
    const existingDays = Array.from(
      new Set(routePlan.blocks.map((block) => block.day)),
    ).filter((day) => Number.isFinite(day));
    const maxDay = existingDays.length > 0 ? Math.max(...existingDays) : 1;
    const cnDay: Record<string, number> = {
      一: 1, 二: 2, 三: 3, 四: 4, 五: 5,
      六: 6, 七: 7, 八: 8, 九: 9, 十: 10,
    };
    const requestedDay = dayMatch
      ? cnDay[dayMatch[1]] ?? Number(dayMatch[1])
      : activeDay !== 'all' && Number.isFinite(activeDay)
        ? activeDay
        : 1;
    const day = Math.min(Math.max(requestedDay, 1), maxDay);
    const timeMatch = content.match(/(\d{1,2}:\d{2})/);
    const sameDayBlocks = routePlan.blocks.filter(
      (block) => Number(block.day) === day,
    );
    const existingRanges = sameDayBlocks
      .map((block) => rangeToMinutes(block.time || ''))
      .filter((range): range is [number, number] => range != null);
    const preferredStart = timeToMinutes(timeMatch ? timeMatch[1] : '09:00') ?? 9 * 60;
    let start = preferredStart;
    if (!timeMatch && pendingAdd.lng != null && pendingAdd.lat != null) {
      const located = sameDayBlocks.filter(
        (block) => block.lng != null && block.lat != null,
      );
      if (located.length > 0) {
        const nearest = located.reduce((best, block) =>
          coordDistance(
            { lng: pendingAdd.lng!, lat: pendingAdd.lat! },
            { lng: block.lng!, lat: block.lat! },
          ) <
          coordDistance(
            { lng: pendingAdd.lng!, lat: pendingAdd.lat! },
            { lng: best.lng!, lat: best.lat! },
          )
            ? block
            : best,
        );
        const end = rangeToMinutes(nearest.time || '')?.[1];
        if (end != null) start = end + 15;
      }
    }
    const overlaps = (candidate: [number, number]) =>
      existingRanges.some(
        (range) => candidate[0] < range[1] && candidate[1] > range[0],
      );
    while (overlaps([start, start + 60]) && start < 23 * 60) {
      start += 30;
    }
    const time = minutesToRange(start);

    const sameDayBlock = routePlan.blocks.find((block) => Number(block.day) === day);
    const date = sameDayBlock?.date ?? routePlan.start_date ?? '';
    const planStyle =
      sameDayBlock?.plan_style || activeStyle || routePlan.styles[0] || '';

    const block: RouteBlock = {
      id: `added-${Date.now()}`,
      plan_style: planStyle,
      day,
      date,
      type: optionToBlockType(pendingAdd),
      time,
      name: pendingAdd.title,
      note: pendingAdd.description ?? '',
      lng: pendingAdd.lng,
      lat: pendingAdd.lat,
    };
    setRoutePlan((prev) =>
      prev
        ? {
            ...prev,
            blocks: [...prev.blocks, block].sort(
              (a, b) =>
                Number(a.day) - Number(b.day) ||
                (a.time || '').localeCompare(b.time || ''),
            ),
            legs: [],
          }
        : prev,
    );
    setPlanItemIds((prev) =>
      prev.includes(pendingAdd.id) ? prev : [...prev, pendingAdd.id],
    );
    setPendingAdd(null);
    addAssistantMessage(
      `已把「${pendingAdd.title}」添加到第 ${day} 天${time ? ` ${time}` : ''}。`,
    );
  }

  function toggleBlock(blockId: string) {
    setSelectedBlocks((prev) => {
      const next = new Set(prev);
      if (next.has(blockId)) {
        next.delete(blockId);
      } else {
        next.add(blockId);
      }
      return next;
    });
  }

  function toggleFoodOption(block: RouteBlock, option: NonNullable<RouteBlock['options']>[number]) {
    setSelectedFoodPoints((prev) => {
      const exists = prev.some((point) => point.id === `food-${option.name}`);
      if (exists) {
        return prev.filter((point) => point.id !== `food-${option.name}`);
      }
      return [
        ...prev,
        {
          id: `food-${option.name}`,
          plan_style: block.plan_style,
          day: block.day,
          date: block.date,
          type: '美食',
          time: block.time,
          name: option.name,
          lng: option.lng,
          lat: option.lat,
        },
      ];
    });
  }

  function nextBatch(tab: OptionType) {
    setBatchIndex((prev) => ({
      ...prev,
      [tab]: (prev[tab] ?? 0) + 1,
    }));
  }

  function nextSocialBatch() {
    setSocialBatch((prev) => prev + 1);
  }

  async function confirmPlan() {
    if (!expandedStyle || !routePlan) return;
    setConfirmedStyle(expandedStyle);
    const userId = localStorage.getItem('currentUser') || '';
    if (userId) {
      void api.reportBehavior(userId, 'confirm', expandedStyle, 'plan');
    }
    if (!userId) {
      setSaveState('error');
      return;
    }
    setSaveState('saving');
    try {
      await api.saveTripMemory({
        user_id: userId,
        destination: routePlan.destination,
        start_date: routePlan.start_date,
        end_date: routePlan.end_date,
        chosen_plan_style: expandedStyle,
        final_plan: routePlan,
        conversation: messages,
      });
      setSaveState('idle');
    } catch {
      setSaveState('error');
    }
  }

  async function submitPlanRating() {
    if (!routePlan || !expandedStyle || planRating == null) return;
    const rating = planRating;
    setSaveState('saving');
    const userId = localStorage.getItem('currentUser') || '';
    if (!userId) {
      setSaveState('error');
      return;
    }
    try {
      await api.saveTripMemory({
        user_id: userId,
        destination: routePlan.destination,
        start_date: routePlan.start_date,
        end_date: routePlan.end_date,
        chosen_plan_style: expandedStyle,
        final_plan: routePlan,
        conversation: messages,
        rating,
        feedback: planFeedback,
      });
      setSaveState('saved');
      void api.reportBehavior(userId, 'rate', expandedStyle, String(rating));
    } catch {
      setSaveState('error');
    }
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
          <span>{item.mode === 'train' ? '高铁' : '航班'} {item.scheduleLabel}</span>
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

    if (item.type === 'event') {
      return (
        <>
          <span>{item.scheduleLabel}</span>
          <span>{item.subtitle}</span>
          <span>{item.price > 0 ? formatPrice(item.price) : '活动'}</span>
        </>
      );
    }

    if (item.type === 'food') {
      return (
        <>
          <span>{item.cuisine}</span>
          <span>{item.rating > 0 ? `${item.rating} ★` : item.businessArea}</span>
          <span>{item.pricePerPerson > 0 ? `${formatPrice(item.pricePerPerson)}/人` : '美食'}</span>
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

          {clarify && (
            <div className="ta-clarify-card">
              <div className="ta-clarify-title">您准备从哪里出发？</div>
              <div className="ta-clarify-row">
                <input
                  value={originInput}
                  onChange={(event) => setOriginInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      event.preventDefault();
                      void submitClarify();
                    }
                  }}
                  placeholder="例如：上海"
                />
                <button
                  type="button"
                  className="ta-clarify-submit"
                  onClick={() => void submitClarify()}
                  disabled={!originInput.trim() || planning}
                >
                  确定
                </button>
              </div>
            </div>
          )}

          {question && (
            <div className="ta-clarify-card">
              <div className="ta-clarify-title">{question.question}</div>
              {question.options.length > 0 ? (
                <div className="ta-clarify-options">
                  {question.options.map((option) => (
                    <button
                      key={option}
                      type="button"
                      className={`ta-clarify-option${
                        selectedOptions.includes(option) ? ' active' : ''
                      }`}
                      onClick={() =>
                        setSelectedOptions((prev) =>
                          prev.includes(option)
                            ? prev.filter((item) => item !== option)
                            : [...prev, option],
                        )
                      }
                    >
                      {option}
                    </button>
                  ))}
                </div>
              ) : (
                <div className="ta-clarify-row">
                  <input
                    type={
                      question.field.includes('date') ||
                      question.field === 'start_date' ||
                      question.field === 'end_date'
                        ? 'date'
                        : 'text'
                    }
                    value={answer}
                    onChange={(event) => setAnswer(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') {
                        event.preventDefault();
                        void submitQuestion();
                      }
                    }}
                    placeholder="请输入"
                  />
                </div>
              )}
              <button
                type="button"
                className="ta-clarify-submit"
                onClick={() => void submitQuestion()}
                disabled={
                  !question.options.length && !answer.trim() ? true : false
                }
              >
                确定
              </button>
            </div>
          )}

          {pendingConfirm && (
            <div className="ta-clarify-card">
              <div className="ta-clarify-title">修改确认</div>
              <p className="ta-confirm-summary">{pendingConfirm.summary}</p>
              <div className="ta-clarify-row">
                <button
                  type="button"
                  className="ta-clarify-submit"
                  onClick={() => void confirmModification()}
                  disabled={planning}
                >
                  确认修改
                </button>
                <button
                  type="button"
                  className="ta-clarify-option"
                  onClick={() => setPendingConfirm(null)}
                >
                  取消
                </button>
              </div>
            </div>
          )}

          <div className="ta-chat-composer">
            <textarea
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={handleComposerKeyDown}
              placeholder="继续补充需求…"
              rows={1}
            />
            {planning ? (
              <button
                type="button"
                className="ta-stop-button"
                onClick={stopPlanStream}
              >
                停止
              </button>
            ) : (
              <button
                type="button"
                className="ta-send-button"
                onClick={handleSend}
                disabled={!draft.trim()}
              >
                发送
              </button>
            )}
          </div>
        </aside>

        {/* 中间：旅行计划 */}
        <section className="ta-plan-card ta-plan-column">
          {expandedStyle && routePlan ? (
            <div className="ta-plan-detail">
              <button type="button" className="ta-plan-detail-back" onClick={() => setExpandedStyle(null)}>
                ← 返回方案列表
              </button>
              <div className="ta-plan-detail-title">
                <strong>{expandedStyle}</strong>
                <p>{routePlan.summaries[expandedStyle] ?? ''}</p>
              </div>
              <div className="ta-plan-detail-total">
                总消费 ¥{' '}
                {routePlan.blocks
                  .filter((block) => block.plan_style === expandedStyle)
                  .reduce((sum, block) => sum + (block.price ?? 0), 0)
                  .toLocaleString()}
              </div>
              {routePlan.budget_status === 'over' && (
                <div className="ta-plan-budget-warning">⚠ 已超出总预算</div>
              )}
              {confirmedStyle === expandedStyle && (
                <div className="ta-plan-confirmed">已确认该计划</div>
              )}
              <div className="ta-plan-detail-scroll">
                {routePlan.blocks
                  .filter((b) => b.plan_style === expandedStyle)
                  .reduce((acc: Array<{ day: number; blocks: RouteBlock[] }>, b) => {
                    const last = acc[acc.length - 1];
                    if (last && Number(last.day) === Number(b.day)) {
                      last.blocks.push(b);
                    } else {
                      acc.push({ day: Number(b.day), blocks: [b] });
                    }
                    return acc;
                  }, [])
                  .map(({ day, blocks: dayBlocks }) => {
                    const dayDate = dayBlocks[0]?.date ?? '';
                    const weather = (weatherData?.days ?? []).find((d) => d.date === dayDate);
                    return (
                      <div key={day} className="ta-plan-style-day">
                        <div className="ta-plan-style-day-label">Day {day}</div>
                        {weather && (
                          <div className="ta-plan-block ta-plan-block-weather">
                            <div className="ta-plan-block-left">
                              <span>天气</span>
                              <span>当日</span>
                            </div>
                            <div className="ta-plan-block-body">
                              <strong>
                                {String(weather.weather ?? '')} {String(weather.temp_min ?? '')}~{String(weather.temp_max ?? '')}°C
                              </strong>
                              <p>湿度 {String(weather.humidity ?? '')}%</p>
                            </div>
                          </div>
                        )}
                        {dayBlocks.map((block) => (
                          <div
                            key={block.id}
                            className={`ta-plan-block ta-plan-block-${block.type}${
                              selectedBlocks.has(block.id) ? ' selected' : ''
                            }`}
                            onClick={() => toggleBlock(block.id)}
                            role="button"
                            tabIndex={0}
                          >
                            <div className="ta-plan-block-left">
                              <span>{block.time}</span>
                              <span>{block.type}</span>
                            </div>
                            <div className="ta-plan-block-body">
                              {block.link ? (
                                <a
                                  className="ta-plan-block-link"
                                  href={block.link}
                                  target="_blank"
                                  rel="noreferrer"
                                >
                                  {block.name}
                                </a>
                              ) : (
                                <strong>{block.name}</strong>
                              )}
                              {block.note && <p>{block.note}</p>}
                              {block.price != null && (
                                <span className="ta-plan-block-price">
                                  ¥ {block.price.toLocaleString()}
                                </span>
                              )}
                              {block.options && block.options.length > 0 && (
                                <div className="ta-plan-block-options">
                                  {block.options.map((option) => {
                                    const active = selectedFoodPoints.some(
                                      (point) => point.id === `food-${option.name}`,
                                    );
                                    return (
                                      <button
                                        key={option.name}
                                        type="button"
                                        className={`ta-food-option${active ? ' active' : ''}`}
                                        onClick={() => toggleFoodOption(block, option)}
                                      >
                                        {option.name}
                                        {option.price != null && option.price > 0
                                          ? ` · ¥${option.price}`
                                          : ''}
                                      </button>
                                    );
                                  })}
                                </div>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    );
                  })}
              </div>
              {confirmedStyle === expandedStyle ? (
                <div className="ta-plan-rating">
                  <div className="ta-plan-rating-label">本次计划满意吗？</div>
                  <div className="ta-plan-rating-buttons">
                    {[
                      { label: '很满意', value: 5 },
                      { label: '满意', value: 4 },
                      { label: '一般', value: 3 },
                      { label: '不满意', value: 1 },
                    ].map((option) => (
                      <button
                        key={option.value}
                        type="button"
                        className={`ta-plan-rating-button${
                          planRating === option.value ? ' active' : ''
                        }`}
                        disabled={saveState === 'saving' || saveState === 'saved'}
                        onClick={() => setPlanRating(option.value)}
                      >
                        {option.label}
                      </button>
                    ))}
                  </div>
                  <button
                    type="button"
                    className="ta-plan-rating-submit"
                    disabled={planRating == null || saveState === 'saving' || saveState === 'saved'}
                    onClick={() => void submitPlanRating()}
                  >
                    提交评价
                  </button>
                  <textarea
                    className="ta-plan-rating-feedback"
                    value={planFeedback}
                    onChange={(event) => setPlanFeedback(event.target.value)}
                    placeholder="补充一点评价（可选）"
                    rows={2}
                  />
                  <div className="ta-plan-rating-status">
                    {saveState === 'saving' && <span>保存中…</span>}
                    {saveState === 'saved' && <span>已保存</span>}
                    {saveState === 'error' && <span>保存失败，请确认已登录</span>}
                  </div>
                </div>
              ) : (
                <button
                  type="button"
                  className="ta-plan-confirm-button"
                  onClick={confirmPlan}
                >
                  确认计划
                </button>
              )}
            </div>
          ) : (
            <>
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

          {travelPlan.length > 0 ? (
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
                    {getOptionIcon(item)}
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
          ) : (
            <div className="ta-plan-empty">
              {routePlan && routePlan.styles.length > 0 ? (
                <div className="ta-plan-styles">
                  <div className="ta-plan-styles-hint">选择一个方案查看详情</div>
                  {routePlan.styles.map((style) => (
                    <button
                      key={style}
                      type="button"
                      className={`ta-plan-style-card${
                        expandedStyle === style ? ' active' : ''
                      }`}
                      onClick={() => setExpandedStyle(style)}
                    >
                      <span className="ta-plan-style-name">{style}</span>
                      <p className="ta-plan-style-summary">
                        {routePlan.summaries[style] ?? ''}
                      </p>
                    </button>
                  ))}
                </div>
              ) : (
                <>
                  <div>从右侧待选行程中添加机票、酒店或景点。</div>
                  <p className="ta-plan-helper">
                    在左侧描述你的行程，例如「宁波 10月1日到10月3日」
                  </p>
                </>
              )}
              <button
                type="button"
                className="ta-plan-mock-button"
                onClick={loadMockData}
              >
                加载测试数据
              </button>
            </div>
          )}
            </>
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
                出行
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
                景点
              </button>
              <button
                type="button"
                className={activeTab === 'event' ? 'active' : ''}
                onClick={() => setActiveTab('event')}
              >
                活动
              </button>
              <button
                type="button"
                className={activeTab === 'food' ? 'active' : ''}
                onClick={() => setActiveTab('food')}
              >
                美食
              </button>
            </div>

            {(activeTab === 'hotel' || activeTab === 'spot' || activeTab === 'food') && (
              <button
                type="button"
                className="ta-batch-button"
                onClick={() => {
                  nextBatch(activeTab);
                  if (activeTab === 'food') nextSocialBatch();
                }}
              >
                换一批
              </button>
            )}
          </div>

          <div className="ta-option-list">
            {activeTab === 'food' && visibleSocialFood.length > 0 && (
              <div className="ta-social-food">
                <div className="ta-social-food-title">抖音 / 小红书笔记</div>
                {visibleSocialFood.map((item, index) => (
                  <a
                    key={`${item.platform}-${index}`}
                    className="ta-social-food-item"
                    href={item.url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <span className="ta-social-food-platform">{item.platform}</span>
                    <span className="ta-social-food-text">{item.title}</span>
                  </a>
                ))}
              </div>
            )}

            {visibleOptions.map((item) => {
              const added = isInPlan(item);

              return (
                <article
                  key={item.id}
                  className={`ta-option-card ${added ? 'selected' : ''}`}
                  onClick={() => setDetailItem(item)}
                >
                  <div className="ta-option-main">
                    {item.image && (
                      <img
                        className="ta-option-image"
                        src={item.image}
                        alt={item.title}
                        loading="lazy"
                      />
                    )}
                    <div className="ta-option-title-row">
                      <div>
                        {item.type === 'flight' && (
                          <span className="ta-carrier-badge">{getCarrierBadge(item)}</span>
                        )}
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
                    {item.url && (
                      <a
                        className="ta-ghost-button"
                        href={item.url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        相关链接
                      </a>
                    )}
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
                      onClick={() => startAdd(item)}
                    >
                      添加到计划
                    </button>
                  </div>
                </article>
              );
            })}
          </div>
        </section>
      </div>

      {/* 悬浮地图：拖动标题栏可以自由移动 */}
      {isMapHidden && (
        <button
          type="button"
          className="ta-map-side-tab"
          onClick={() => setIsMapHidden(false)}
          aria-label="显示地图"
        >
          <span className="ta-map-side-arrow">◀</span>
          <span className="ta-map-side-label">地图</span>
        </button>
      )}

      {!isMapHidden && (
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
              className="ta-map-hide-button"
              onPointerDown={(event) => event.stopPropagation()}
              onClick={(event) => {
                event.stopPropagation();
                setIsMapHidden(true);
              }}
              aria-label="隐藏地图"
            >
              隐藏
            </button>
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
            <TripMap
              blocks={[...styleBlocks, ...selectedFoodPoints]}
              legs={styleLegs}
              day={activeDay}
            />
          </>
        ) : (
          <div className="ta-map-placeholder ta-floating-map-placeholder">
            <span>{planning ? '正在规划路线…' : '地图区域'}</span>
            {planning && <p>生成后会在这里显示每天的路线</p>}
          </div>
        )}
      </section>
      )}

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
                      <span>出发时间</span>
                      <strong>{detailItem.departTime}</strong>
                    </div>
                    <div>
                      <span>到达时间</span>
                      <strong>{detailItem.arriveTime}</strong>
                    </div>
                    <div>
                      <span>{detailItem.mode === 'train' ? '车次' : '航司'}</span>
                      <strong>{detailItem.carrier}{detailItem.code}</strong>
                    </div>
                    <div>
                      <span>时长</span>
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

              {detailItem.type === 'food' && (
                <>
                  <div className="ta-detail-grid">
                    <div>
                      <span>菜系</span>
                      <strong>{detailItem.cuisine || '—'}</strong>
                    </div>
                    <div>
                      <span>评分</span>
                      <strong>{detailItem.rating > 0 ? `${detailItem.rating} ★` : '—'}</strong>
                    </div>
                    <div>
                      <span>人均</span>
                      <strong>{detailItem.pricePerPerson > 0 ? formatPrice(detailItem.pricePerPerson) : '—'}</strong>
                    </div>
                    <div>
                      <span>商圈</span>
                      <strong>{detailItem.businessArea || '—'}</strong>
                    </div>
                  </div>

                  <div className="ta-detail-foot">
                    <span>地址：{detailItem.address || detailItem.location || '—'}</span>
                    <strong>
                      {detailItem.detailUrl && (
                        <a href={detailItem.detailUrl} target="_blank" rel="noreferrer">
                          查看详情
                        </a>
                      )}
                      {detailItem.mapUrl && (
                        <a href={detailItem.mapUrl} target="_blank" rel="noreferrer">
                          地图
                        </a>
                      )}
                    </strong>
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
