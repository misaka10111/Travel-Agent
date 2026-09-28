import type {
  ChatMessage,
  ChatResponse,
  Destination,
  FoodSearchRequest,
  Trip,
  TripCreatePayload,
  UserProfile,
} from './types';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api';

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers ?? {}),
    },
    ...options,
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`请求失败 (${response.status}): ${body}`);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

export const api = {
  listTrips: (userId?: string) =>
    request<Trip[]>(`/trips${userId ? `?user_id=${encodeURIComponent(userId)}` : ''}`),
  getTrip: (id: number) => request<Trip>(`/trips/${id}`),
  createTrip: (payload: TripCreatePayload) =>
    request<Trip>('/trips', { method: 'POST', body: JSON.stringify(payload) }),
  updateTrip: (id: number, payload: Partial<TripCreatePayload>) =>
    request<Trip>(`/trips/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
  deleteTrip: (id: number) =>
    request<void>(`/trips/${id}`, { method: 'DELETE' }),
  listDestinations: () => request<Destination[]>('/destinations'),
  saveProfile: (userId: string, profile: UserProfile) =>
    request<UserProfile>('/profile', {
      method: 'POST',
      body: JSON.stringify({ user_id: userId, ...profile }),
    }),
  getProfile: (userId: string) => request<UserProfile>(`/profile/${userId}`),
  reportBehavior: (userId: string, action: string, target = '', detail = '') =>
    request<void>('/behavior-signals', {
      method: 'POST',
      body: JSON.stringify({ user_id: userId, action, target, detail }),
    }),
  plan: (payload: {
    query?: string;
    destination?: string;
    start_date?: string;
    end_date?: string;
    profile?: unknown;
    basic?: unknown;
    answers?: unknown[];
    food_options?: FoodSearchRequest;
    modify?: unknown;
  }) =>
    request<Record<string, unknown>>('/plan', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  chat: (messages: ChatMessage[]) =>
    request<ChatResponse>('/agent/chat', {
      method: 'POST',
      body: JSON.stringify({ messages }),
    }),
  sendCode: (phone: string) =>
    request<{ phone: string; code: string; dev: boolean }>('/auth/send-code', {
      method: 'POST',
      body: JSON.stringify({ phone }),
    }),
  login: (phone: string, code: string) =>
    request<{ user_id: string; phone: string; nickname: string; is_new: boolean }>(
      '/auth/login',
      { method: 'POST', body: JSON.stringify({ phone, code }) },
    ),
  saveTripMemory: (payload: {
    user_id: string;
    destination: string;
    start_date: string;
    end_date: string;
    chosen_plan_style?: string;
    final_plan?: unknown;
    rating?: number;
    feedback?: string;
  }) =>
    request('/trip-memory', { method: 'POST', body: JSON.stringify(payload) }),
};
