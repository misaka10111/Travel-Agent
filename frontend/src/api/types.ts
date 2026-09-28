export interface ItineraryItem {
  id: number;
  trip_id: number;
  day: number;
  title: string;
  description: string;
  location: string;
  start_time: string | null;
  end_time: string | null;
}

export interface Trip {
  id: number;
  user_id: string;
  title: string;
  destination: string;
  origin: string;
  start_date: string;
  end_date: string;
  travelers: string;
  status: string;
  budget: number | null;
  budget_tiers: string[];
  purposes: string[];
  notes: string;
  created_at: string;
  updated_at: string;
  items: ItineraryItem[];
}

export interface Destination {
  id: number;
  name: string;
  country: string;
  description: string;
  tags: string;
}

export interface TripCreatePayload {
  user_id?: string;
  title: string;
  destination: string;
  origin?: string;
  start_date: string;
  end_date: string;
  travelers?: string;
  status?: string;
  budget?: number | null;
  budget_tiers?: string[];
  purposes?: string[];
  notes?: string;
  items?: Array<{
    day: number;
    title: string;
    description?: string;
    location?: string;
  }>;
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

export interface ChatResponse {
  reply: string;
}

export interface FoodLocation {
  longitude: number;
  latitude: number;
  coordinate_system?: 'GCJ-02';
}

export interface FoodSearchRequest {
  destination?: string | null;
  query?: string | null;
  location?: FoodLocation | null;
  radius_m?: number;
  cuisines?: string[];
  keywords?: string[];
  /** 人民币/人/餐。旅行总预算仍放在 basic.total_budget。 */
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
  location: FoodLocation | null;
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
  reviews: null;
  missing_fields: string[];
  source: 'amap';
  fetched_at: string;
}

export interface FoodSearchMeta {
  warnings?: string[];
  resolved_query?: Partial<FoodSearchRequest> & Record<string, unknown>;
  source?: 'amap';
  coordinate_system?: 'GCJ-02';
  fetched_at?: string;
}

export interface UserProfile {
  age_group: string;
  gender: string;
  identity: string;
  city: string;
  travel_style: string[];
}

export interface TripInfo {
  destination: string;
  origin: string;
  start_date: string;
  end_date: string;
  travelers: string;
  companions: string[];
  budget_tiers: string[];
  total_budget: string;
  purposes: string[];
  special_needs: string[];
}
