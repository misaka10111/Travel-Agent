import { useEffect, useRef, useState } from 'react';
import type { FormEvent, KeyboardEvent } from 'react';

type Recommendation = {
  destination: string;
  country: string;
  dates: string;
  totalCost: number;
  flightCost: number;
  hotelCost: number;
  weather: string;
  weatherScore: number;
  event: string;
  eventScore: number;
  totalScore: number;
  highlight: string;
  airline: string;
  flightDuration: string;
  hotelNightly: number;
  weatherType: string;
  activityDetail: string;
  source: string;
  fetchedAt: string;
};

type MessageItem = {
  id: string;
  kind: 'message';
  role: 'user' | 'assistant';
  text: string;
};

type ResultsItem = {
  id: string;
  kind: 'results';
  recommendations: Recommendation[];
};

type ConversationItem = MessageItem | ResultsItem;

const baseRecommendations: Recommendation[] = [
  {
    destination: '首尔',
    country: '韩国',
    dates: '11 月 5 日 - 11 月 9 日',
    totalCost: 4900,
    flightCost: 2100,
    hotelCost: 2800,
    weather: '16°C · 降雨风险 20%',
    weatherScore: 0.85,
    event: 'Seoul Music Week',
    eventScore: 0.92,
    totalScore: 0.87,
    highlight: '价格、天气和音乐活动的整体平衡最好。',
    airline: '示例航空 · 直飞',
    flightDuration: '约 3 小时 35 分',
    hotelNightly: 700,
    weatherType: '历史同期均值',
    activityDetail: '与你的“演唱会 / 现场音乐”兴趣高度匹配。',
    source: 'Mock Flight + Weather + Event Snapshot',
    fetchedAt: '2026-09-27 14:20',
  },
  {
    destination: '东京',
    country: '日本',
    dates: '10 月 23 日 - 10 月 27 日',
    totalCost: 5350,
    flightCost: 2350,
    hotelCost: 3000,
    weather: '19°C · 降雨风险 28%',
    weatherScore: 0.8,
    event: 'Tokyo Live Circuit',
    eventScore: 0.76,
    totalScore: 0.82,
    highlight: '城市体验丰富，整体稳定，但预算略高。',
    airline: '示例航空 · 直飞',
    flightDuration: '约 4 小时 10 分',
    hotelNightly: 750,
    weatherType: '历史同期均值',
    activityDetail: '活动数量丰富，但与你的音乐兴趣匹配度略低于首尔。',
    source: 'Mock Flight + Hotel + Event Snapshot',
    fetchedAt: '2026-09-27 14:20',
  },
  {
    destination: '曼谷',
    country: '泰国',
    dates: '11 月 1 日 - 11 月 5 日',
    totalCost: 4450,
    flightCost: 1750,
    hotelCost: 2700,
    weather: '30°C · 降雨风险 42%',
    weatherScore: 0.62,
    event: 'Bangkok Sound Nights',
    eventScore: 0.88,
    totalScore: 0.79,
    highlight: '预算最友好，活动匹配高，但天气风险更明显。',
    airline: '示例航空 · 直飞',
    flightDuration: '约 3 小时',
    hotelNightly: 675,
    weatherType: '历史同期均值',
    activityDetail: '音乐活动匹配度很高，但降雨风险是主要取舍。',
    source: 'Mock Travel Snapshot',
    fetchedAt: '2026-09-27 14:20',
  },
];

const surprisePick = {
  destination: '大阪',
  country: '日本',
  dates: '10 月 29 日 - 11 月 2 日',
  totalCost: 5050,
  score: 0.84,
  event: 'Indie Autumn Festival',
  reason: '不是最便宜的选择，但现场音乐匹配度突出，而且综合表现仍然很接近前三名。',
};

function makeId() {
  return `${Date.now()}-${Math.random()}`;
}

function ScoreBadge({ score }: { score: number }) {
  return <span className="agent-score-badge">{Math.round(score * 100)} 分</span>;
}

function RecommendationCard({
  trip,
  displayRank,
}: {
  trip: Recommendation;
  displayRank: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const rankLabel = ['🥇', '🥈', '🥉'][displayRank - 1] ?? `#${displayRank}`;

  function toggleExpanded() {
    setExpanded((value) => !value);
  }

  function handleCardKeyDown(event: KeyboardEvent<HTMLElement>) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      toggleExpanded();
    }
  }

  return (
    <article
      className={`agent-recommendation-card agent-clickable-card ${expanded ? 'expanded' : ''}`}
      onClick={toggleExpanded}
      onKeyDown={handleCardKeyDown}
      role="button"
      tabIndex={0}
      aria-expanded={expanded}
    >
      <div className="agent-card-topline">
        <span className="agent-rank">{rankLabel} 推荐 {displayRank}</span>
        <ScoreBadge score={trip.totalScore} />
      </div>

      <div>
        <h3>{trip.destination}</h3>
        <p className="agent-card-subtitle">
          {trip.country} · {trip.dates}
        </p>
      </div>

      <div className="agent-price-row">
        <strong>HKD {trip.totalCost.toLocaleString()}</strong>
        <span>预计总费用</span>
      </div>

      <div className="agent-metric-list">
        <div>
          <span>✈️ 机票</span>
          <strong>HKD {trip.flightCost.toLocaleString()}</strong>
        </div>
        <div>
          <span>🏨 酒店</span>
          <strong>HKD {trip.hotelCost.toLocaleString()}</strong>
        </div>
        <div>
          <span>🌤️ 天气</span>
          <strong>{trip.weather}</strong>
        </div>
        <div>
          <span>🎵 活动</span>
          <strong>{trip.event}</strong>
        </div>
      </div>

      <p className="agent-card-reason">{trip.highlight}</p>

      <div className="agent-card-expand-hint">
        <span>{expanded ? '点击收起详情' : '点击卡片查看详情'}</span>
        <span aria-hidden="true">{expanded ? '↑' : '↓'}</span>
      </div>

      {expanded && (
        <div className="agent-card-details" onClick={(event) => event.stopPropagation()}>
          <div className="agent-detail-grid">
            <div>
              <span>航班</span>
              <strong>{trip.airline}</strong>
              <small>{trip.flightDuration}</small>
            </div>
            <div>
              <span>酒店均价</span>
              <strong>HKD {trip.hotelNightly}/晚</strong>
              <small>4 晚示例价格</small>
            </div>
            <div>
              <span>天气数据</span>
              <strong>{trip.weatherType}</strong>
              <small>天气评分 {Math.round(trip.weatherScore * 100)}</small>
            </div>
            <div>
              <span>活动匹配</span>
              <strong>{Math.round(trip.eventScore * 100)} 分</strong>
              <small>{trip.activityDetail}</small>
            </div>
          </div>

          <div className="agent-score-breakdown">
            <div>
              <span>天气</span>
              <div><i style={{ width: `${trip.weatherScore * 100}%` }} /></div>
              <strong>{Math.round(trip.weatherScore * 100)}</strong>
            </div>
            <div>
              <span>活动</span>
              <div><i style={{ width: `${trip.eventScore * 100}%` }} /></div>
              <strong>{Math.round(trip.eventScore * 100)}</strong>
            </div>
            <div>
              <span>综合</span>
              <div><i style={{ width: `${trip.totalScore * 100}%` }} /></div>
              <strong>{Math.round(trip.totalScore * 100)}</strong>
            </div>
          </div>

          <div className="agent-source-box">
            <span>数据来源：{trip.source}</span>
            <span>获取时间：{trip.fetchedAt}</span>
          </div>
        </div>
      )}
    </article>
  );
}

function ComparisonTable({ recommendations }: { recommendations: Recommendation[] }) {
  return (
    <div className="agent-inline-comparison">
      <div className="agent-section-heading-row">
        <div>
          <span className="agent-section-label">SIDE-BY-SIDE</span>
          <h2>快速对比</h2>
        </div>
        <span className="agent-table-note">Mock data</span>
      </div>

      <div className="agent-table-wrap">
        <table className="agent-comparison-table">
          <thead>
            <tr>
              <th>方案</th>
              {recommendations.map((trip) => (
                <th key={trip.destination}>{trip.destination}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>预计总费用</td>
              {recommendations.map((trip) => (
                <td key={trip.destination}>HKD {trip.totalCost.toLocaleString()}</td>
              ))}
            </tr>
            <tr>
              <td>天气评分</td>
              {recommendations.map((trip) => (
                <td key={trip.destination}>{Math.round(trip.weatherScore * 100)}</td>
              ))}
            </tr>
            <tr>
              <td>活动匹配</td>
              {recommendations.map((trip) => (
                <td key={trip.destination}>{Math.round(trip.eventScore * 100)}</td>
              ))}
            </tr>
            <tr className="agent-total-row">
              <td>综合评分</td>
              {recommendations.map((trip) => (
                <td key={trip.destination}>{Math.round(trip.totalScore * 100)}</td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

function RecommendationResults({ recommendations }: { recommendations: Recommendation[] }) {
  return (
    <div className="agent-chat-results">
      <div className="agent-response-shell">
        <div className="agent-response-header compact">
          <div className="agent-avatar">TR</div>
          <div>
            <div className="agent-response-title-row">
              <strong>推荐结果</strong>
              <span className="agent-status-badge complete">分析完成</span>
            </div>
            <p>点击任意推荐卡片，可展开查看航班、酒店、评分和数据来源。</p>
          </div>
        </div>

        <div className="agent-recommendation-grid">
          {recommendations.map((trip, index) => (
            <RecommendationCard
              key={`${trip.destination}-${index}`}
              trip={trip}
              displayRank={index + 1}
            />
          ))}
        </div>

        <div className="agent-surprise-card">
          <div className="agent-surprise-icon">✦</div>
          <div className="agent-surprise-main">
            <div className="agent-card-topline">
              <span className="agent-surprise-label">SURPRISE PICK</span>
              <ScoreBadge score={surprisePick.score} />
            </div>
            <h3>{surprisePick.destination}</h3>
            <p className="agent-card-subtitle">
              {surprisePick.country} · {surprisePick.dates}
            </p>
            <p className="agent-surprise-reason">{surprisePick.reason}</p>
            <div className="agent-surprise-meta">
              <span>约 HKD {surprisePick.totalCost.toLocaleString()}</span>
              <span>🎵 {surprisePick.event}</span>
            </div>
          </div>
        </div>

        <ComparisonTable recommendations={recommendations} />
      </div>
    </div>
  );
}

function inferPreference(message: string) {
  const normalized = message.toLowerCase();

  if (['价格', '便宜', '预算', '省钱', 'cost', 'price', 'cheap'].some((word) => normalized.includes(word))) {
    return 'price';
  }

  if (['天气', '下雨', '避雨', '气候', '舒服', 'weather', 'rain'].some((word) => normalized.includes(word))) {
    return 'weather';
  }

  if (['活动', '演唱会', '音乐', 'festival', 'concert', 'event'].some((word) => normalized.includes(word))) {
    return 'event';
  }

  if (['均衡', '平衡', '都可以', 'balanced'].some((word) => normalized.includes(word))) {
    return 'balanced';
  }

  return 'other';
}

function reorderRecommendations(preference: string) {
  const next = [...baseRecommendations];

  if (preference === 'price') {
    return next.sort((a, b) => a.totalCost - b.totalCost);
  }

  if (preference === 'weather') {
    return next.sort((a, b) => b.weatherScore - a.weatherScore);
  }

  if (preference === 'event') {
    return next.sort((a, b) => b.eventScore - a.eventScore);
  }

  return next.sort((a, b) => b.totalScore - a.totalScore);
}

export function AgentPage() {
  const [draft, setDraft] = useState(
    '十月中到十一月中想出去玩 4-5 天，东京、首尔、曼谷都可以，预算 6000 港币，喜欢演唱会，不想遇到太多雨。',
  );
  const [thinking, setThinking] = useState(false);
  const [hasStarted, setHasStarted] = useState(false);
  const [conversation, setConversation] = useState<ConversationItem[]>([]);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const textarea = composerRef.current;
    if (!textarea) return;

    textarea.style.height = '44px';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 144)}px`;
  }, [draft]);

  function pushItems(...items: ConversationItem[]) {
    setConversation((current) => [...current, ...items]);
  }

  async function mockThink(delay = 900) {
    setThinking(true);
    await new Promise((resolve) => window.setTimeout(resolve, delay));
    setThinking(false);
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();

    const message = draft.trim();
    if (!message || thinking) return;

    setDraft('');

    pushItems({
      id: makeId(),
      kind: 'message',
      role: 'user',
      text: message,
    });

    if (!hasStarted) {
      await mockThink(1100);

      const initialRecommendations = reorderRecommendations('balanced');

      pushItems(
        {
          id: makeId(),
          kind: 'message',
          role: 'assistant',
          text:
            '我先根据你现在提供的信息做了一版初步比较。综合预算、天气和现场音乐活动，首尔目前最均衡；东京整体稳定但价格略高；曼谷最省预算，不过天气风险更明显。',
        },
        {
          id: makeId(),
          kind: 'results',
          recommendations: initialRecommendations,
        },
        {
          id: makeId(),
          kind: 'message',
          role: 'assistant',
          text:
            '如果你想让我把结果再调得更贴近你的偏好：你更看重天气、价格，还是活动体验？直接在下面告诉我就可以。',
        },
      );

      setHasStarted(true);
      return;
    }

    await mockThink(800);

    const preference = inferPreference(message);

    if (preference === 'price') {
      const next = reorderRecommendations('price');
      pushItems(
        {
          id: makeId(),
          kind: 'message',
          role: 'assistant',
          text:
            '好的，我把“价格”放到更高优先级。按当前 Mock 数据，曼谷会升到第一，因为总费用最低；首尔仍然是价格和体验之间比较均衡的选择。',
        },
        {
          id: makeId(),
          kind: 'results',
          recommendations: next,
        },
        {
          id: makeId(),
          kind: 'message',
          role: 'assistant',
          text:
            '还想继续调整吗？例如你可以说“尽量不要下雨”“活动更重要”，或者直接问某个城市的详细情况。',
        },
      );
      return;
    }

    if (preference === 'weather') {
      const next = reorderRecommendations('weather');
      pushItems(
        {
          id: makeId(),
          kind: 'message',
          role: 'assistant',
          text:
            '可以，我提高了天气因素的优先级。首尔的天气评分最高，东京其次；曼谷虽然价格更低，但降雨风险会让它在这个偏好下排得更后。',
        },
        {
          id: makeId(),
          kind: 'results',
          recommendations: next,
        },
        {
          id: makeId(),
          kind: 'message',
          role: 'assistant',
          text:
            '你还可以继续告诉我，例如“预算最多 5000 港币”或“我更想看演唱会”，我会继续调整。',
        },
      );
      return;
    }

    if (preference === 'event') {
      const next = reorderRecommendations('event');
      pushItems(
        {
          id: makeId(),
          kind: 'message',
          role: 'assistant',
          text:
            '明白，我把活动体验放到更高优先级。首尔和曼谷的音乐活动匹配度更高，因此会比东京更靠前。',
        },
        {
          id: makeId(),
          kind: 'results',
          recommendations: next,
        },
        {
          id: makeId(),
          kind: 'message',
          role: 'assistant',
          text:
            '如果你愿意，还可以继续补充更具体的兴趣，比如“独立音乐”“大型演唱会”或“音乐节”。',
        },
      );
      return;
    }

    if (preference === 'balanced') {
      const next = reorderRecommendations('balanced');
      pushItems(
        {
          id: makeId(),
          kind: 'message',
          role: 'assistant',
          text:
            '好的，我保持价格、天气和活动之间的均衡权重。当前首尔仍然是整体最均衡的方案。',
        },
        {
          id: makeId(),
          kind: 'results',
          recommendations: next,
        },
        {
          id: makeId(),
          kind: 'message',
          role: 'assistant',
          text:
            '还想继续调整哪个条件？你可以直接像聊天一样告诉我。',
        },
      );
      return;
    }

    pushItems(
      {
        id: makeId(),
        kind: 'message',
        role: 'assistant',
        text:
          `收到：“${message}”。现在还是纯前端 Mock 阶段，所以我只对“价格 / 天气 / 活动 / 均衡”做了简单模拟。接入真实 Agent 后，这里会把你的这句话连同之前的对话一起发给后端。`,
      },
      {
        id: makeId(),
        kind: 'message',
        role: 'assistant',
        text:
          '你可以继续试试：“天气更重要”“想再便宜一点”或“活动体验最重要”。',
      },
    );
  }

  function handleComposerKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  return (
    <section className="agent-page">
      <div className="agent-hero">
        <span className="agent-kicker">✦ TripRadar · AI Travel Planner</span>
        <h1>
          把模糊的旅行想法，
          <span>变成清晰的选择。</span>
        </h1>
        <p>
          先给你一版可以直接比较的答案，再像聊天一样继续问你真正关心的条件。
        </p>
      </div>

      <section className="agent-search-card agent-gpt-shell">
        <div className="agent-search-heading">
          <div>
            <span className="agent-section-label">CONVERSATION</span>
            <h2>{hasStarted ? '继续和 TripRadar 对话' : '这次想怎么旅行？'}</h2>
          </div>
          <span className="agent-status-badge ready">Mock data</span>
        </div>

        {conversation.length > 0 && (
          <div className="agent-conversation-log agent-conversation-log-v3" aria-live="polite">
            {conversation.map((item) => {
              if (item.kind === 'results') {
                return (
                  <RecommendationResults
                    key={item.id}
                    recommendations={item.recommendations}
                  />
                );
              }

              return (
                <div key={item.id} className={`agent-message ${item.role}`}>
                  {item.role === 'assistant' && <span className="agent-mini-avatar">TR</span>}
                  <div className="agent-message-bubble">{item.text}</div>
                </div>
              );
            })}

            {thinking && (
              <div className="agent-message assistant">
                <span className="agent-mini-avatar">TR</span>
                <div className="agent-message-bubble agent-inline-thinking">
                  <span />
                  <span />
                  <span />
                  正在思考
                </div>
              </div>
            )}
          </div>
        )}

        <form className="agent-chat-composer agent-chat-composer-v3" onSubmit={handleSubmit}>
          <textarea
            ref={composerRef}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={handleComposerKeyDown}
            placeholder={
              hasStarted
                ? '继续说你的想法，例如：天气更重要 / 想再便宜一点 / 活动体验最重要…'
                : '例如：11 月想从香港出发玩 4 天，东京或首尔都可以，预算 6000 港币，希望天气舒服并且有现场音乐活动。'
            }
            rows={1}
          />

          <div className="agent-search-footer">
            <span>Ctrl / ⌘ + Enter 发送 · 当前为纯前端 Mock 对话</span>
            <button
              className="agent-primary-button"
              type="submit"
              disabled={thinking || !draft.trim()}
            >
              {thinking ? '正在思考…' : hasStarted ? '发送' : '开始规划'}
              {!thinking && <span aria-hidden="true">→</span>}
            </button>
          </div>
        </form>
      </section>
    </section>
  );
}