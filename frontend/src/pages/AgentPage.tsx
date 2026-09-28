import { useEffect, useRef, useState } from 'react';
import type { FormEvent, KeyboardEvent } from 'react';
import { useLocation } from 'react-router-dom';
import { api } from '../api/client';

type RealBlock = {
  id: string;
  day: number;
  date: string;
  type: string;
  time: string;
  name: string;
  note: string;
  link: string;
};

type RealPlan = {
  style: string;
  summary: string;
  blocks: RealBlock[];
};

type MessageItem = {
  id: string;
  role: 'user' | 'assistant';
  text: string;
};

const KNOWN_CITIES = [
  '东京', '首尔', '大阪', '曼谷', '北京', '上海', '杭州', '宁波',
  '广州', '深圳', '成都', '西安', '重庆', '三亚', '香港',
];

function makeId() {
  return `${Date.now()}-${Math.random()}`;
}

const RATINGS = [
  { label: '很满意', value: 5 },
  { label: '满意', value: 4 },
  { label: '一般', value: 3 },
  { label: '不满意', value: 1 },
];

const FIELD_QUESTIONS: Record<string, string> = {
  origin: '您准备从哪里出发？',
  travelers: '几个人一起出行？',
  total_budget: '这次预算大概多少元？',
  purposes: '这次主要想做什么？比如美食、文化、自然风光。',
  start_date: '计划哪天出发？',
  end_date: '哪天返程？',
};

function hasDateMention(query: string): boolean {
  return /\d+\s*[天日月号]|\d{1,2}月|\d{4}[-/.]\d{1,2}/.test(query);
}

function detectModifyScope(query: string): string | null {
  if (query.includes('酒店') || query.includes('住宿')) return '酒店';
  if (query.includes('景点') || query.includes('活动')) return '景点';
  if (query.includes('美食') || query.includes('餐厅') || query.includes('饭店') || query.includes('餐')) return '美食';
  if (query.includes('交通') || query.includes('机票') || query.includes('高铁')) return '交通';
  return null;
}

function getMissingFields(
  basic: Record<string, unknown> | undefined,
  query: string,
): string[] {
  const missing: string[] = [];
  if (!basic?.origin) missing.push('origin');
  if (!basic?.travelers) missing.push('travelers');
  if (!basic?.total_budget) missing.push('total_budget');
  if (!basic?.purposes || (basic.purposes as unknown[]).length === 0) missing.push('purposes');
  if (!basic?.start_date && !hasDateMention(query)) missing.push('start_date');
  if (!basic?.end_date && !hasDateMention(query)) missing.push('end_date');
  return missing;
}

function buildSupplement(collected: Record<string, string>): string {
  const parts: string[] = [];
  if (collected.origin) parts.push(`从${collected.origin}出发`);
  if (collected.travelers) {
    parts.push(collected.travelers.includes('人') ? collected.travelers : `${collected.travelers}人`);
  }
  if (collected.total_budget) parts.push(`预算${collected.total_budget}元`);
  if (collected.purposes) parts.push(`主要想做${collected.purposes}`);
  if (collected.start_date) parts.push(`${collected.start_date}出发`);
  if (collected.end_date) parts.push(`${collected.end_date}返程`);
  return parts.join('，');
}

function extractDestination(query: string): string {
  for (const city of KNOWN_CITIES) {
    if (query.includes(city)) return city;
  }
  return '北京';
}

function PlanCard({
  plan,
  onSelect,
  confirming,
  onConfirm,
  onRate,
}: {
  plan: RealPlan;
  onSelect: () => void;
  confirming: boolean;
  onConfirm: (plan: RealPlan) => void;
  onRate: (plan: RealPlan, rating: number) => void;
}) {
  return (
    <article
      className="agent-recommendation-card agent-clickable-card"
      onClick={onSelect}
      role="button"
      tabIndex={0}
    >
      <div className="agent-card-topline">
        <span className="agent-rank">{plan.style}</span>
      </div>
      <h3>{plan.style}</h3>
      <p className="agent-card-reason">{plan.summary}</p>
      <div className="agent-card-expand-hint">
        <span>点击查看完整行程</span>
        <span aria-hidden="true">→</span>
      </div>
      <button
        type="button"
        className="agent-confirm-btn"
        onClick={(e) => {
          e.stopPropagation();
          onConfirm(plan);
        }}
      >
        {confirming ? '请选择评价' : '确认此方案'}
      </button>
      {confirming && (
        <div className="agent-rating" onClick={(e) => e.stopPropagation()}>
          {RATINGS.map((r) => (
            <button
              key={r.value}
              type="button"
              className="agent-rating-btn"
              onClick={() => onRate(plan, r.value)}
            >
              {r.label}
            </button>
          ))}
        </div>
      )}
    </article>
  );
}

function PlanDetail({
  plan,
  destination,
  dates,
  selectedBlocks,
  onToggleBlock,
  searchData,
  onBack,
}: {
  plan: RealPlan;
  destination: string;
  dates: string;
  selectedBlocks: Set<string>;
  onToggleBlock: (id: string) => void;
  searchData: Record<string, unknown> | null;
  onBack: () => void;
}) {
  const days = Array.from(new Set(plan.blocks.map((block) => block.day)));

  return (
    <div className="agent-plan-detail">
      <button type="button" className="agent-back-btn" onClick={onBack}>
        ← 返回方案列表
      </button>

      <div className="agent-response-header compact">
        <div className="agent-avatar">TR</div>
        <div>
          <div className="agent-response-title-row">
            <strong>{plan.style} · 行程计划</strong>
            <span className="agent-status-badge complete">已展开</span>
          </div>
          <p>
            {destination} · {dates}
          </p>
        </div>
      </div>

      {days.map((day) => {
        const dayBlocks = plan.blocks.filter((block) => block.day === day);
        const dayDate = dayBlocks[0]?.date ?? '';
        const weatherDays = (searchData?.weather as { days?: Array<Record<string, unknown>> } | undefined)?.days ?? [];
        const weather = weatherDays.find((d) => d.date === dayDate);
        const events = (searchData?.events ?? []) as Array<{ title?: string }>;
        return (
        <div key={day} className="agent-block-day">
          <div className="agent-block-day-label">Day {day}</div>
          <div className="agent-block-list">
            {weather && (
              <div className="agent-block agent-block-weather">
                <div className="agent-block-left">
                  <span className="agent-block-time">天气</span>
                  <span className="agent-block-type">天气</span>
                </div>
                <div className="agent-block-body">
                  <strong>{String(weather.weather ?? '')} {String(weather.temp_min ?? '')}~{String(weather.temp_max ?? '')}°C</strong>
                  <p>湿度 {String(weather.humidity ?? '')}%</p>
                </div>
              </div>
            )}
            {events.length > 0 && (
              <div className="agent-block agent-block-event">
                <div className="agent-block-left">
                  <span className="agent-block-time">活动</span>
                  <span className="agent-block-type">活动</span>
                </div>
                <div className="agent-block-body">
                  <strong>热点活动</strong>
                  <p>{events.slice(0, 2).map((e) => e.title).filter(Boolean).join('；')}</p>
                </div>
              </div>
            )}
            {dayBlocks
              .map((block) => (
                <div
                  key={block.id}
                  className={`agent-block agent-block-${block.type}${
                    selectedBlocks.has(block.id) ? ' agent-block-selected' : ''
                  }`}
                  onClick={() => onToggleBlock(block.id)}
                  role="button"
                  tabIndex={0}
                >
                  <div className="agent-block-left">
                    <span className="agent-block-time">{block.time}</span>
                    <span className="agent-block-type">{block.type}</span>
                  </div>
                  <div className="agent-block-body">
                    {block.link ? (
                      <strong>
                        <a
                          href={block.link}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="agent-block-link"
                          onClick={(e) => e.stopPropagation()}
                        >
                          {block.name}
                        </a>
                      </strong>
                    ) : (
                      <strong>{block.name}</strong>
                    )}
                    <p>{block.note}</p>
                  </div>
                </div>
              ))}
          </div>
        </div>
        );
      })}
    </div>
  );
}

export function AgentPage() {
  const location = useLocation();
  const [draft, setDraft] = useState('');
  const [messages, setMessages] = useState<MessageItem[]>([]);
  const [plans, setPlans] = useState<RealPlan[]>([]);
  const [destination, setDestination] = useState('');
  const [dates, setDates] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [selectedPlan, setSelectedPlan] = useState<RealPlan | null>(null);
  const [pendingQuery, setPendingQuery] = useState<string | null>(null);
  const [pendingFields, setPendingFields] = useState<string[]>([]);
  const [collected, setCollected] = useState<Record<string, string>>({});
  const [selectedBlocks, setSelectedBlocks] = useState<Set<string>>(new Set());
  const [confirmingPlanId, setConfirmingPlanId] = useState<string | null>(null);
  const [searchData, setSearchData] = useState<Record<string, unknown> | null>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const autoStartedRef = useRef(false);

  useEffect(() => {
    const textarea = composerRef.current;
    if (!textarea) return;
    textarea.style.height = '44px';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 144)}px`;
  }, [draft]);

  function toggleBlock(id: string) {
    setSelectedBlocks((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }

  function confirmPlan(plan: RealPlan) {
    setConfirmingPlanId((prev) => (prev === plan.style ? null : plan.style));
  }

  async function ratePlan(plan: RealPlan, rating: number) {
    const userId = localStorage.getItem('currentUser') || '';
    const [start, end] = dates.includes(' ~ ') ? dates.split(' ~ ') : ['', ''];
    try {
      await api.saveTripMemory({
        user_id: userId,
        destination,
        start_date: start,
        end_date: end,
        chosen_plan_style: plan.style,
        final_plan: plan,
        rating,
      });
      setConfirmingPlanId(null);
      setMessages((current) => [
        ...current,
        {
          id: makeId(),
          role: 'assistant',
          text: '已确认方案并记录你的评价，感谢反馈！',
        },
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function generate(
    query: string,
    profile: unknown,
    basic: Record<string, unknown> | undefined,
    modify?: unknown,
    destination?: string,
    start_date?: string,
    end_date?: string,
  ) {
    try {
      const raw = await api.plan({ query, profile, basic, modify, destination, start_date, end_date });
      setSearchData((raw.search ?? null) as Record<string, unknown> | null);
      const planData = (raw.plan ?? {}) as {
        destination?: string;
        start_date?: string;
        end_date?: string;
        plans?: Array<{ style?: string; summary?: string }>;
        blocks?: RealBlock[];
      };
      const planList = planData.plans ?? [];
      const allBlocks = planData.blocks ?? [];

      const mapped: RealPlan[] = planList.map((p) => ({
        style: p.style ?? '方案',
        summary: p.summary ?? '',
        blocks: allBlocks.filter((b) => (b as unknown as { plan_style?: string }).plan_style === p.style),
      }));

      const dest = planData.destination ?? extractDestination(query);
      const start = planData.start_date ?? '';
      const end = planData.end_date ?? '';

      setPlans(mapped);
      setDestination(dest);
      setDates(start && end ? `${start} ~ ${end}` : '');
      setMessages((current) => [
        ...current,
        {
          id: makeId(),
          role: 'assistant',
          text: `已为「${dest}」生成 ${mapped.length} 个方案，点击右侧卡片查看完整行程。`,
        },
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function localModify(
    blockList: RealBlock[],
    instruction: string,
    profile: unknown,
    basic: Record<string, unknown> | undefined,
  ) {
    try {
      if (blockList.length === 0) return;

      const [start, end] = dates.includes(' ~ ') ? dates.split(' ~ ') : ['', ''];
      const raw = await api.plan({
        destination,
        start_date: start,
        end_date: end,
        profile,
        basic,
        modify: { blocks: blockList, instruction },
      });
      const modified = (raw as { blocks?: RealBlock[] }).blocks ?? [];
      const byId = new Map(modified.map((b) => [b.id, b]));

      setPlans((prev) =>
        prev.map((p) => ({
          ...p,
          blocks: p.blocks.map((b) => byId.get(b.id) ?? b),
        })),
      );
      setSelectedPlan((prev) =>
        prev ? { ...prev, blocks: prev.blocks.map((b) => byId.get(b.id) ?? b) } : prev,
      );
      setSelectedBlocks(new Set());
      setMessages((current) => [
        ...current,
        { id: makeId(), role: 'assistant', text: '已根据你的意见修改选中块。' },
      ]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  async function runPlan(query: string) {
    setMessages((current) => [...current, { id: makeId(), role: 'user', text: query }]);
    setDraft('');
    setLoading(true);
    setError('');
    setSelectedPlan(null);

    let profile: unknown = undefined;
    try {
      profile = JSON.parse(localStorage.getItem('userProfile') || '{}');
    } catch {
      profile = undefined;
    }

    let basic: Record<string, unknown> | undefined = undefined;
    try {
      basic = JSON.parse(localStorage.getItem('tripInfo') || '{}');
    } catch {
      basic = undefined;
    }

    // 关键：若 tripInfo 的目的地与当前 query 说的目的地不一致，视为新旅行，忽略旧 tripInfo
    if (basic?.destination && !query.includes(String(basic.destination))) {
      basic = undefined;
    }

    // 修改流程：已选中 block，用户输入视为修改意见
    if (selectedBlocks.size > 0) {
      const selectedBlockList: RealBlock[] = [];
      for (const p of plans) {
        for (const b of p.blocks) {
          if (selectedBlocks.has(b.id)) selectedBlockList.push(b);
        }
      }
      await localModify(selectedBlockList, query.trim(), profile, basic);
      return;
    }

    // 修改流程：没选块，但输入是修改指令（含「修改/改」），按类型范围收集块
    if (query.includes('修改') || query.includes('改')) {
      if (plans.length === 0) {
        setLoading(false);
        setMessages((current) => [
          ...current,
          {
            id: makeId(),
            role: 'assistant',
            text: '还没有可修改的方案，请先告诉我想去哪里，生成方案后再修改。',
          },
        ]);
        return;
      }
      const scope = detectModifyScope(query);
      const blockList = plans
        .flatMap((p) => p.blocks)
        .filter((b) => !scope || b.type === scope);
      if (blockList.length === 0) {
        setLoading(false);
        setMessages((current) => [
          ...current,
          { id: makeId(), role: 'assistant', text: '没有找到与你说的内容对应的块。' },
        ]);
        return;
      }
      await localModify(blockList, query.trim(), profile, basic);
      return;
    }

    // 正在追问中：这轮是回答上一题
    if (pendingFields.length > 0) {
      const answeredField = pendingFields[0];
      const nextCollected = { ...collected, [answeredField]: query.trim() };
      const remaining = pendingFields.slice(1);
      setCollected(nextCollected);

      if (remaining.length > 0) {
        setPendingFields(remaining);
        setLoading(false);
        setMessages((current) => [
          ...current,
          { id: makeId(), role: 'assistant', text: FIELD_QUESTIONS[remaining[0]] },
        ]);
        return;
      }

      setPendingFields([]);
      const supplement = buildSupplement(nextCollected);
      const finalQuery = pendingQuery ? `${pendingQuery}，${supplement}` : query;
      setPendingQuery(null);
      await generate(finalQuery, profile, basic);
      return;
    }

    // 第一次输入：检测缺失字段，逐个追问
    const missing = getMissingFields(basic, query);
    if (missing.length > 0) {
      setPendingQuery(query);
      setPendingFields(missing);
      setCollected({});
      setLoading(false);
      setMessages((current) => [
        ...current,
        { id: makeId(), role: 'assistant', text: FIELD_QUESTIONS[missing[0]] },
      ]);
      return;
    }

    await generate(query, profile, basic);
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const query = draft.trim();
    if (!query || loading) return;
    runPlan(query);
  }

  useEffect(() => {
    const state = location.state as { autostart?: boolean } | null;
    if (!state?.autostart || autoStartedRef.current) return;
    autoStartedRef.current = true;

    let tripInfo: { destination?: string; start_date?: string; end_date?: string } = {};
    try {
      tripInfo = JSON.parse(localStorage.getItem('tripInfo') || '{}');
    } catch {
      tripInfo = {};
    }
    const dest = tripInfo.destination?.trim();
    const start = tripInfo.start_date;
    const end = tripInfo.end_date;
    if (dest && start) {
      const query = end ? `${dest} ${start} 到 ${end}` : `${dest} ${start}`;
      setDraft(query);
      runPlan(query);
    } else {
      setMessages((current) => [
        ...current,
        {
          id: makeId(),
          role: 'assistant',
          text: '还没有填写目的地和日期，请在下方输入框补充。',
        },
      ]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  return (
    <section className="agent-page">
      <div className="agent-hero">
        <span className="agent-kicker">✦ TravelAgent · AI Travel Planner</span>
        <h1>
          把模糊的旅行想法，
          <span>变成清晰的选择。</span>
        </h1>
        <p>告诉我你想去哪里，我会为你生成可比较的旅行方案。</p>
      </div>

      <div className="agent-workspace">
        <section className="agent-search-card agent-chat-panel">
          <div className="agent-search-heading">
            <div>
              <span className="agent-section-label">CONVERSATION</span>
              <h2>这次想怎么旅行？</h2>
            </div>
            <span className="agent-status-badge ready">实时生成</span>
          </div>

          <div className="agent-conversation-log agent-conversation-log-v3" aria-live="polite">
            {messages.length === 0 && (
              <div className="agent-message assistant">
                <span className="agent-mini-avatar">TR</span>
                <div className="agent-message-bubble">
                  在左边告诉我你的旅行想法，我会在右边生成可比较的方案。
                </div>
              </div>
            )}

            {messages.map((message) => (
              <div key={message.id} className={`agent-message ${message.role}`}>
                {message.role === 'assistant' && <span className="agent-mini-avatar">TR</span>}
                <div className="agent-message-bubble">{message.text}</div>
              </div>
            ))}

            {loading && (
              <div className="agent-message assistant">
                <span className="agent-mini-avatar">TR</span>
                <div className="agent-message-bubble agent-inline-thinking">
                  <span />
                  <span />
                  <span />
                  正在生成计划（约需 1~5 分钟）
                </div>
              </div>
            )}
          </div>

          <form className="agent-chat-composer agent-chat-composer-v3" onSubmit={handleSubmit}>
            <textarea
              ref={composerRef}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={handleComposerKeyDown}
              placeholder="例如：我想去杭州玩五天"
              rows={1}
            />
            <div className="agent-search-footer">
              <span>Ctrl / ⌘ + Enter 发送 · 会调用真实的多 Agent 流水线</span>
              <button
                className="agent-primary-button"
                type="submit"
                disabled={loading || !draft.trim()}
              >
                {loading ? '生成中…' : '生成计划'}
                {!loading && <span aria-hidden="true">→</span>}
              </button>
            </div>
          </form>
        </section>

        <section className="agent-search-card agent-plan-panel">
          <div className="agent-search-heading">
            <div>
              <span className="agent-section-label">PLAN</span>
              <h2>{destination ? `${destination} 的行程` : '生成的计划'}</h2>
            </div>
            {selectedPlan ? (
              <span className="agent-status-badge complete">查看详情</span>
            ) : plans.length > 0 ? (
              <span className="agent-status-badge complete">已生成</span>
            ) : (
              <span className="agent-status-badge ready">等待输入</span>
            )}
          </div>

          {error && <div className="login-error">{error}</div>}

          {selectedPlan ? (
            <PlanDetail
              plan={selectedPlan}
              destination={destination}
              dates={dates}
              selectedBlocks={selectedBlocks}
              onToggleBlock={toggleBlock}
              searchData={searchData}
              onBack={() => setSelectedPlan(null)}
            />
          ) : plans.length > 0 ? (
            <div className="agent-recommendation-grid">
              {plans.map((plan) => (
                <PlanCard
                  key={plan.style}
                  plan={plan}
                  onSelect={() => setSelectedPlan(plan)}
                  confirming={confirmingPlanId === plan.style}
                  onConfirm={confirmPlan}
                  onRate={ratePlan}
                />
              ))}
            </div>
          ) : (
            <div className="agent-plan-empty">
              <div className="agent-surprise-icon">✦</div>
              <h3>计划会出现在这里</h3>
              <p>在左侧描述你的旅行想法，我会在这里生成可比较的方案。</p>
            </div>
          )}
        </section>
      </div>
    </section>
  );
}
