const BASE = import.meta.env.VITE_API_BASE_URL ?? '/api';

export interface Place {
  place_id: string;
  name: string;
  category: string;
  coordinates: { longitude: number; latitude: number; crs: string } | null;
  address: string | null;
  facts: Record<string, { value: unknown; source_ref: string; unknown_reason?: string }>;
}
export interface Stop {
  stop_id: string; place_id: string; category: string;
  start_minute: number | null; end_minute: number | null;
  dwell_minutes: number; locked: boolean; child_place_ids: string[]; reason: string;
}
export interface Route {
  leg_id: string; from_place_id: string; to_place_id: string;
  mode: string; status: string; duration_seconds: number | null; distance_meters: number | null;
  provider: string; requested_departure_at: string; queried_at: string;
  unknown_reason: string | null; warnings: string[];
  steps: Array<{ line_name: string | null; instruction: string | null }>;
}
export interface Draft {
  plan_id: string; version: number; status: string;
  days: Array<{ day_index: number; date: string | null; hotel_place_id: string | null; stops: Stop[]; routes: Route[]; notes: string[] }>;
  missing_requirements: string[]; assumptions: string[];
  audit: { hard_status: string; experience_status: string; reviewed: boolean; issues: Array<{ code: string; severity: string; message: string; day_index: number | null }> } | null;
  travel_offers: Array<{ category: string; direction: string; name: string | null; price_display: string | null; currency: string | null; price_scope: string; segments: Array<{ service_no: string | null; origin_station: string | null; destination_station: string | null; departure_at: string | null; arrival_at: string | null }> }>;
}
export interface SessionView {
  state: {
    session_id: string; state_version: number; status: string; attention_reason: string | null;
    retry_generation: number; retry_child_ref: string | null;
    agent_message: string | null; candidates: Place[]; candidates_need_refresh: boolean; candidates_stale: boolean;
    plan_needs_refresh: boolean; current_plan: Draft | null; current_plan_ref: { plan_id: string; version: number } | null;
    coverage: Record<string, number>; supplier_status: Record<string, string>;
    intent_snapshot: { destination: { label: string }; dates: { duration_days: number | null; start_date: string | null }; preferences: { interests: string[]; dietary_preferences: string[]; lodging_preferences: string[] } };
    pending_questions: Array<{ question_id: string; state_version: number; prompt: string; mode: string; options: Array<{ option_id: string; label: string }> }>;
    selections: Array<{ selection_id: string; place_id: string; decision: string; evidence: { source: string; source_ref: string; confirmation: string } }>;
    plan_history: Array<{ plan_id: string; version: number; change_reason: string }>;
  };
  usage: { model_calls: number; map_calls: number; steps: number; model_limit: number; map_limit: number; step_limit: number };
}
export interface SessionCredential { id: string; token: string }

async function call<T>(path: string, token?: string, body?: unknown, method = 'GET'): Promise<T> {
  const response = await fetch(`${BASE}/sessions${path}`, {
    method, headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) {
    let code = '';
    try { const error = await response.json(); code = error.code ?? JSON.stringify(error.detail); } catch { code = '服务暂不可用'; }
    throw new Error(`请求失败 (${response.status})：${code}`);
  }
  return response.json() as Promise<T>;
}

export const planning = {
  create: (message: string, profile: unknown) => call<SessionView & { access_token: string }>('', undefined, { message, profile, task_scope: 'itinerary' }, 'POST'),
  get: (c: SessionCredential) => call<SessionView>(`/${c.id}`, c.token),
  run: (c: SessionCredential, version: number) => call<SessionView>(`/${c.id}/run`, c.token, { request_id: crypto.randomUUID(), base_state_version: version }, 'POST'),
  retry: (c: SessionCredential, version: number) => call<SessionView & { access_token: string }>(`/${c.id}/retry`, c.token, { request_id: crypto.randomUUID(), base_state_version: version }, 'POST'),
  edit: (c: SessionCredential, version: number, update: unknown) => call<SessionView>(`/${c.id}`, c.token, { request_id: crypto.randomUUID(), base_state_version: version, ...(update as object) }, 'PATCH'),
  answer: (c: SessionCredential, q: SessionView['state']['pending_questions'][number], text?: string, optionId?: string) => call<SessionView>(`/${c.id}/answers`, c.token, { answer: { request_id: crypto.randomUUID(), question_id: q.question_id, state_version: q.state_version, ...(text ? { text } : {}), option_ids: optionId ? [optionId] : [] } }, 'POST'),
  cancel: (c: SessionCredential, version: number) => call<SessionView>(`/${c.id}/cancel`, c.token, { request_id: crypto.randomUUID(), base_state_version: version }, 'POST'),
};
