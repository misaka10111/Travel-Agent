/** 餐饮独立接口示例：可复制到 frontend/src/api/food.ts。API key 只放后端。 */
export interface GeoPoint {
  longitude: number;
  latitude: number;
  coordinate_system?: 'GCJ-02';
}

export interface FoodSearchRequest {
  destination?: string | null;
  query?: string | null;
  location?: GeoPoint | null;
  radius_m?: number;
  cuisines?: string[];
  keywords?: string[];
  /** 人民币/人/餐；不是整次旅行预算。 */
  max_price_per_person?: number | null;
  min_rating?: number | null;
  limit?: number;
  include_unknown_price?: boolean;
  include_unknown_rating?: boolean;
}

export interface Restaurant {
  id: string;
  name: string;
  title: string;
  content: string;
  address: string;
  location: GeoPoint | null;
  price_per_person: number | null;
  currency: 'CNY';
  price_unit: 'person/meal';
  rating: number | null;
  rating_scale: 5;
  cuisine: string | null;
  cuisine_source: string | null;
  category: string;
  typecode: string;
  tags: string[];
  business_area: string | null;
  telephone: string | null;
  website: string | null;
  url: string;
  opening_hours: string | null;
  distance_m: number | null;
  distance_kind: 'straight_line' | null;
  /** 高德搜索接口不提供用户评论正文，不能把评分当作评论。 */
  reviews: null;
  missing_fields: string[];
  source: 'amap';
  fetched_at: string;
}

export interface FoodSearchResponse {
  destination: string;
  resolved_query: Record<string, unknown>;
  food: Restaurant[];
  warnings: string[];
  source: 'amap';
  coordinate_system: 'GCJ-02';
  fetched_at: string;
}

export interface FoodValidationDetail {
  loc?: Array<string | number>;
  msg?: string;
  type?: string;
}

interface ApiErrorBody {
  error?: {
    code?: string;
    message?: string;
    details?: FoodValidationDetail[];
  };
  detail?: string | FoodValidationDetail[];
}

export class FoodSearchError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly code?: string,
    public readonly details?: FoodValidationDetail[],
  ) {
    super(message);
    this.name = 'FoodSearchError';
  }
}

/**
 * 本地开发 baseUrl 为 http://127.0.0.1:8001。
 * 后端反向代理到同源 /api/search/food 时，baseUrl 传空字符串。
 * signal 可用于取消过期请求，避免旧查询覆盖新的页面结果。
 */
export async function searchFood(
  input: FoodSearchRequest,
  baseUrl = 'http://127.0.0.1:8001',
  signal?: AbortSignal,
): Promise<FoodSearchResponse> {
  if (!input.destination?.trim() && !input.query?.trim()) {
    throw new FoodSearchError(422, '请填写目的地或餐饮查询。');
  }
  const response = await fetch(`${baseUrl.replace(/\/$/, '')}/api/search/food`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
    signal,
  });
  if (!response.ok) {
    const body: ApiErrorBody | null = await response.json().catch(() => null);
    const detail = body?.error?.details ?? body?.detail;
    const detailMessage = typeof detail === 'string'
      ? detail
      : detail?.map((entry) => entry.msg ?? '参数无效').join('；');
    throw new FoodSearchError(
      response.status,
      body?.error?.message ?? detailMessage ?? `餐饮查询失败（${response.status}）`,
      body?.error?.code,
      Array.isArray(detail) ? detail : undefined,
    );
  }
  return (await response.json()) as FoodSearchResponse;
}

/** 演示输入；中心坐标是高德 GCJ-02，不可直接换成浏览器原始定位。 */
export const exampleFoodRequest: FoodSearchRequest = {
  destination: '杭州',
  query: '西湖附近的杭帮菜，人均不超过100元',
  location: { longitude: 120.1488, latitude: 30.2424, coordinate_system: 'GCJ-02' },
  radius_m: 3000,
  cuisines: ['杭帮菜'],
  keywords: [],
  max_price_per_person: 100,
  min_rating: null,
  limit: 10,
  include_unknown_price: false,
  include_unknown_rating: false,
};

/** 将餐饮事实传给 PlanAgent；画像和行程日期由负责规划的后端附加。 */
export function mergeFoodIntoSearch(
  existingSearch: Record<string, unknown>,
  response: FoodSearchResponse,
): Record<string, unknown> {
  return {
    ...existingSearch,
    destination: response.destination,
    food: response.food,
    food_meta: {
      warnings: response.warnings,
      resolved_query: response.resolved_query,
      fetched_at: response.fetched_at,
      source: response.source,
      coordinate_system: response.coordinate_system,
    },
  };
}
