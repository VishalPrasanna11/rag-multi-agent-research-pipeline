export type Mode = "full_cycle" | "pre_only" | "post_only" | "question";

export interface Citation { id: string; source: string; title: string; url?: string | null; as_of: string;
  retrieved_at: string; locator?: string | null; license_note?: string | null }
export interface Fact { key: string; label: string; value: number | string | null; unit: string; period?: string | null;
  as_of: string; citation_id: string; derived_from: string[]; formula?: string | null; status: "ok" | "unavailable";
  needs?: string | null }
export interface Section { id: string; title: string; markdown: string; citations: string[] }
export interface Confidence { level: "low" | "medium" | "high"; method: string; score?: number | null;
  explanation: string; drivers: string[] }
export interface AgentOutput { agent: string; status: string; sections: Section[]; facts: string[]; unknowns: string[];
  confidence: Confidence; timing_ms: number }
export interface Scenario { name: "bull" | "base" | "bear"; probability: number; probability_method: string;
  base_rate_n?: number | null; narrative: string; evidence: string[]; move_range_pct?: [number, number] | null }
export interface Brief { headline: string; setup: string[]; scenarios: Scenario[]; what_would_change_view: string[];
  questions_for_call: string[]; key_risks: string[]; unknowns: string[]; freshness: { source: string; as_of: string | null }[];
  confidence: Confidence; disclaimer: string; validated: boolean; validation_note?: string }
export interface CritiqueIssue { kind: string; severity: string; agent?: string | null; detail: string }
export interface CritiqueRound { round: number; issues: CritiqueIssue[]; action: string; tokens: number }
export interface EarningsEvent { ticker: string; cik?: string; company?: string; fiscal_period?: string | null;
  date?: string | null; time_of_day: string; date_status: string }
export interface Report { id: string; run_id: string; ticker: string; mode: Mode; query?: string | null; as_of: string;
  created_at: string; event: EarningsEvent; brief?: Brief | null; agents: AgentOutput[]; facts: Record<string, Fact>;
  citations: Record<string, Citation>; critique_history: CritiqueRound[]; review_status: string; fixture: boolean;
  cost_usd: number; disclaimer: string; notices?: string[]; schema_version: string; saved?: boolean; shared?: boolean;
  reviews?: { decision: string; notes?: string | null; reviewer: string; created_at: string }[] }
export interface RunSummary { id: string; ticker: string; mode: Mode; status: string; query?: string | null;
  created_at: string; finished_at?: string | null; report_id?: string | null; fixture: boolean; cost_usd: number;
  review_status?: string | null }
export interface StreamEvent { run_id: string; seq: number; ts: string; type: string; agent?: string | null;
  payload: Record<string, any> }
export interface Meta { version: string; schema_version: string; fixture_mode: boolean; durable_storage: boolean;
  auth: { configured: boolean; clerk_publishable_key?: string | null; allow_anonymous: boolean };
  needs: Record<string, string>; disclaimer: string; data_sources: { name: string; use: string; license: string }[] }
export interface ApiError { code: string; message: string; retryable: boolean; request_id: string; agent?: string }
