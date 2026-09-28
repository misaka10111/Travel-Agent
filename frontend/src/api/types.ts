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
