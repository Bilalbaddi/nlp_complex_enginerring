export type Role = "commissioner" | "department_head" | "ward_officer" | "analyst" | "admin";

export interface User {
  id: number;
  email: string;
  full_name: string;
  role: Role;
  department_id: number | null;
  ward_id: number | null;
  is_active: boolean;
  created_at?: string;
  last_login_at?: string | null;
}

export interface ApiErrorBody {
  code: string;
  message: string;
  details?: unknown;
}

export interface Envelope<T> {
  success: boolean;
  data: T | null;
  error: ApiErrorBody | null;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: User;
}

export interface HealthStatus {
  status: string;
  llm_demo_mode: boolean;
  llm_provider: string;
}

export interface Page<T> {
  items: T[];
  meta: { page: number; page_size: number; total: number; pages: number };
}

export interface Ward {
  id: number;
  code: string;
  name: string;
  population: number;
  area_sq_km: number;
  road_km: number;
  water_connections: number;
  centroid_lat: number | null;
  centroid_lon: number | null;
}

export interface Department {
  id: number;
  code: string;
  name: string;
  category: string | null;
  sla_hours: number;
}

export interface Complaint {
  id: number;
  external_id: string | null;
  description: string;
  language: string | null;
  category: string | null;
  status: string;
  channel: string | null;
  ward_id: number | null;
  ward_name: string | null;
  department_name: string | null;
  address: string | null;
  submitted_at: string;
  resolved_at: string | null;
  resolution_hours: number | null;
  sla_hours: number | null;
  sla_breached: boolean | null;
  predicted_category: string | null;
  category_confidence: number | null;
  sentiment_score: number | null;
  urgency_score: number | null;
  needs_review: boolean;
  review_reason: string | null;
  duplicate_of_id: number | null;
  entities: Record<string, string[]> | null;
  citizen_name: string | null;
  citizen_phone: string | null;
}

/** Labelled provenance attached to every LLM output. */
export interface AiLabel {
  ai_generated: boolean;
  model?: string;
  provider?: string;
  cached?: boolean;
  demo_mode?: boolean;
  sources?: string[];
  as_of?: string;
}

export interface KpiValues {
  complaints: number;
  resolution_rate: number | null;
  sla_compliance_pct: number | null;
  avg_resolution_hours: number | null;
  median_resolution_hours?: number | null;
  satisfaction: number | null;
  complaints_per_10k: number | null;
  repeat_rate_pct: number | null;
  efficiency_score: number | null;
  open_backlog: number | null;
}

export interface KpiSummary {
  as_of: string;
  period: { from: string; to: string; months: number };
  current: KpiValues;
  previous_period: KpiValues;
  same_period_last_year: KpiValues;
  change_vs_previous_pct: Record<string, number | null>;
  change_vs_last_year_pct: Record<string, number | null>;
  higher_is_better: Record<string, boolean>;
}

export interface KpiTrendPoint extends KpiValues {
  month: string;
}

export interface RankItem extends KpiValues {
  id: number;
  code: string;
  name: string;
  category?: string;
  rank: number;
}

export interface Rankings {
  by: string;
  metric: string;
  higher_is_better: boolean;
  period: { from: string; to: string };
  items: RankItem[];
}

export interface SlaRow {
  key: string | number;
  name?: string;
  code?: string | null;
  complaints: number;
  breached: number;
  breach_rate_pct: number;
  avg_resolution_hours: number | null;
  open: number;
}

export interface Distribution {
  window_from: string;
  buckets: { label: string; count: number }[];
  percentiles_hours: Record<string, number | null>;
}

export interface Backlog {
  total_open: number;
  total_overdue: number;
  by_age: { bucket: string; count: number; overdue: number }[];
  by_department: { category: string; name: string; open: number; overdue: number }[];
  by_ward: { ward_id: number; name: string | null; open: number; overdue: number }[];
}

export interface RepeatHotspot {
  category: string;
  ward: string | null;
  lat: number;
  lon: number;
  complaints: number;
  last_at: string;
}

export interface GapItem {
  ward_id: number;
  code: string;
  name: string;
  complaints: number;
  breach_rate: number;
  delay_ratio: number;
  backlog_share: number;
  repeat_rate: number;
  dissatisfaction: number;
  satisfaction: number;
  sgi: number;
  rank: number;
}

export interface GapIndex {
  period: { from: string; to: string };
  weights: Record<string, number>;
  items: GapItem[];
}

export interface EmergingIssue {
  id: string;
  category: string;
  severity: number;
  trend: "rising" | "steady" | "falling";
  recent_complaints: number;
  wards: { ward_id: number; name: string | null; code: string | null; z: number; ratio: number; recent: number; baseline_weekly: number }[];
  samples: { id: number; ward: string | null; text: string; submitted_at: string }[];
  summary: { text: string } & Partial<AiLabel>;
}

export interface EmergingFeed {
  as_of: string;
  thresholds: Record<string, number>;
  issues: EmergingIssue[];
}

export interface Topic {
  topic: number;
  terms: string[];
  recent: number;
  previous: number;
  growth: number;
  examples: string[];
}

export interface ForecastVolume {
  category: string | null;
  ward_id: number | null;
  horizon: number;
  history: { month: string; complaints: number }[];
  forecast: { month: string; mean: number; lo: number; hi: number }[];
  model: string | null;
  backtest: { mae?: number; mape_pct?: number | null; models?: Record<string, { mae: number; mape_pct: number | null }> } | null;
}

export interface BacktestAll {
  horizon_months: number;
  avg_best_mape_pct: number | null;
  series: { series: string; category: string | null; mean_monthly: number; best: string | null; best_mae: number | null; best_mape_pct: number | null }[];
}

export interface ResourceForecast {
  months: string[];
  total_crew_days: number[];
  categories: {
    category: string;
    department: string;
    crew_days_per_complaint: number;
    crew_days_needed: number[];
    capacity_crew_days_per_month: number;
    shortfall: number[];
    utilisation_pct: (number | null)[];
  }[];
  assumptions: Record<string, unknown>;
}

export interface WardRisk {
  items: { ward_id: number; code: string; name: string; risk_score: number; level: string; forecast_next_3_months: number; gap_index: number; growth: number }[];
  weights: Record<string, number>;
}

export interface ScenarioSeries {
  capacity_per_month: number;
  final_backlog: number;
  final_sla_compliance_pct: number;
  months: { month: string; demand: number; capacity: number; backlog: number; utilisation_pct: number; sla_compliance_pct: number }[];
}

export interface WhatIf {
  category: string;
  current: { open_backlog: number; capacity_per_month: number; demand_per_month: number; sla_compliance_pct: number };
  baseline: ScenarioSeries;
  scenario: ScenarioSeries;
  delta: { final_backlog: number; final_sla_compliance_pct: number };
  assumptions: Record<string, unknown>;
}

export interface Choropleth {
  type: "FeatureCollection";
  features: {
    type: "Feature";
    geometry: GeoJSON.Geometry;
    properties: { ward_id: number; code: string; name: string; population: number; value: number | null; metric: string };
  }[];
  metric: string;
  min: number | null;
  max: number | null;
  higher_is_better: boolean;
  period: { from: string; to: string };
}

export interface Heatmap {
  points: [number, number, number][];
  max: number;
}

export interface Hotspots {
  clusters: { id: number; complaints: number; center: [number, number]; dominant_category: string; category_share: number; ward: string | null; hull: GeoJSON.Polygon | null }[];
  n_points: number;
  params: { eps_m: number; min_samples: number; algorithm: string };
}

export interface Brief {
  month: string;
  as_of: string;
  markdown: string;
  label: AiLabel;
}

export interface ModelRun {
  id: number;
  name: string;
  task: string;
  version: string;
  metrics: Record<string, unknown>;
  params: Record<string, unknown>;
  n_train: number;
  n_test: number;
  train_seconds: number;
  is_active: boolean;
  created_at: string;
}

export interface LlmUsage {
  days: number;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  avg_latency_ms: number;
  cache_hit_rate_pct: number;
  errors: number;
  by_purpose: { purpose: string; model: string; calls: number; tokens: number; avg_latency_ms: number; cache_hits: number }[];
  daily: { day: string; calls: number; tokens: number }[];
}

export interface DocumentItem {
  id: number;
  title: string;
  doc_type: string;
  source_filename: string | null;
  num_chunks: number;
  created_at: string;
}

export interface IngestionLog {
  id: number;
  source_type: string;
  filename: string | null;
  status: string;
  rows_total: number;
  rows_ok: number;
  rows_rejected: number;
  rows_duplicate: number;
  errors: Record<string, unknown> | null;
  started_at: string;
  finished_at: string | null;
}

export interface AuditLog {
  id: number;
  user_email: string | null;
  action: string;
  resource_type: string | null;
  resource_id: string | null;
  ip_address: string | null;
  created_at: string;
}

export interface Job {
  id: number;
  type: string;
  status: string;
  attempts: number;
  max_attempts: number;
  error: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface RagAnswer {
  answer: string;
  sources: { n: number; title: string; page: number | null; score: number; retrieval: string; snippet: string }[];
  label: AiLabel;
}
