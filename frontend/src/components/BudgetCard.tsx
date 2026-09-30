import { useEffect, useState } from 'react';
import type { Draft, SessionView } from '../api/planning';

type Intent = SessionView['state']['intent_snapshot'];
type Field = 'hotel' | 'intercity' | 'activities' | 'food';
type Amounts = Record<Field, string>;
const EMPTY: Amounts = { hotel: '', intercity: '', activities: '', food: '' };
const FIELDS: Array<[Field, string]> = [
  ['hotel', '住宿总额'], ['intercity', '往返城际交通总额'],
  ['activities', '门票及市内交通总额'], ['food', '餐饮总额'],
];

function amount(value: string | number | null | undefined) {
  if (value === '' || value == null) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function yuan(value: number) {
  return `¥${new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 }).format(value)}`;
}

function initial(key: string): Amounts {
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) ?? 'null');
    return saved && typeof saved === 'object' ? { ...EMPTY, ...saved } : { ...EMPTY };
  } catch { return { ...EMPTY }; }
}

export function BudgetCard({ plan, intent, sessionId }: { plan: Draft | null; intent: Intent; sessionId: string }) {
  const key = `travelBudgetAssumptions:${sessionId}`;
  const [values, setValues] = useState<Amounts>(() => initial(key));
  useEffect(() => { sessionStorage.setItem(key, JSON.stringify(values)); }, [key, values]);

  const party = intent.party.count_status === 'exact' ? intent.party.count : null;
  const days = intent.dates.duration_days ?? plan?.days.length ?? null;
  const money = intent.budget.money;
  const target = money?.currency === 'CNY' && money.scope === 'trip_total' ? amount(money.amount) :
    money?.currency === 'CNY' && money.scope === 'per_person' && party ? (amount(money.amount) ?? 0) * party :
    money?.currency === 'CNY' && money.scope === 'per_person_per_day' && party && days ? (amount(money.amount) ?? 0) * party * days : null;
  const food = plan?.cost_summary;
  const foodReference = amount(food?.food_reference_for_party);
  const completeFoodReference = !!food && food.planned_meals > 0 &&
    food.meals_with_reference === food.planned_meals && foodReference !== null;
  const manual = Object.fromEntries(FIELDS.map(([field]) => [field, amount(values[field])])) as Record<Field, number | null>;
  const foodAmount = manual.food ?? (completeFoodReference ? foodReference : null);
  const items = [manual.hotel, manual.intercity, manual.activities, foodAmount];
  const subtotal = items.reduce<number>((sum, item) => sum + (item ?? 0), 0);
  const complete = !!plan && items.every(item => item !== null);
  const filled = items.filter(item => item !== null).length;

  return <section className="ta-budget-card" aria-label="旅行预算参考">
    <div className="ta-budget-head"><strong>预算参考</strong><span>{target !== null ?
      `目标总预算 ${yuan(target)}${money?.scope === 'per_person_per_day' ? `（${money.amount} 元 × ${party} 人 × ${days} 天）` : ''}` :
      money ? `用户预算 ${money.amount} ${money.currency} · ${money.scope === 'per_person_per_day' ? '每人每天' : money.scope === 'per_person' ? '每人' : '计价范围待确认'}` : '尚未填写目标预算'}</span></div>
    {food && <p>餐饮人均消费参考：{foodReference !== null ? `${yuan(foodReference)} / ${party} 人` :
      food.food_reference_per_person !== null ? `${yuan(Number(food.food_reference_per_person))} / 人` : '暂无金额'}
      <small>已覆盖 {food.meals_with_reference}/{food.planned_meals} 餐；来自高德地点信息，不是实时菜单报价。</small></p>}
    {filled > 0 && <p className="ta-budget-total">{complete ? '按假设粗略合计' : `已填写项目小计（${filled}/4 项）`}：{yuan(subtotal)}
      {complete && target !== null && <small>{subtotal > target ? `高于目标约 ${yuan(subtotal - target)}` : `低于目标约 ${yuan(target - subtotal)}`}</small>}</p>}
    <details><summary>填写费用假设，计算粗略预算</summary>
      <div className="ta-budget-fields">{FIELDS.map(([field, label]) => <label key={field}>{label}（元）
        <input type="number" min="0" step="0.01" inputMode="decimal" value={values[field]}
          placeholder={field === 'food' && completeFoodReference ? `可沿用${yuan(foodReference!)}` : '待填写'}
          onChange={event => setValues(previous => ({ ...previous, [field]: event.target.value }))} />
      </label>)}</div>
      <p>空白项不计入小计。餐饮参考仅在所有计划餐次都有金额且人数已知时自动计入；其他金额由你填写，不代表供应商报价。</p>
    </details>
  </section>;
}
