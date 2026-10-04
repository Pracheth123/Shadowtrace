/**
 * HTTP API client and guest identity.
 *
 * The server issues a guest bearer token once (POST /api/guest). It is kept in
 * this browser's localStorage and sent on every call; the server resolves the
 * candidate from it and never trusts an id the browser names. Limits, stated
 * in the UI too: one browser, no recovery, and clearing site data loses access.
 */

const WS_HOST = import.meta.env.VITE_WS_HOST || `${location.hostname}:8000`;
export const HTTP_BASE = `${location.protocol}//${WS_HOST}`;

const TOKEN_KEY = "shadowtrace.guest_token";

/** Upload rules — must match intake/documents.py. */
export const UPLOAD_ACCEPT = ".pdf,.docx,.txt,.md";
export const UPLOAD_LABEL = "PDF, DOCX, plain text or Markdown";
export const UPLOAD_MAX_MB = 5;

export class ApiError extends Error {
  status: number;
  recovery?: string;
  body: Record<string, unknown>;
  constructor(status: number, body: Record<string, unknown>) {
    super(String(body.error ?? `Request failed (${status})`));
    this.status = status;
    this.recovery = body.recovery ? String(body.recovery) : undefined;
    this.body = body;
  }
}

function readToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

function writeToken(token: string) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode: the token lives for this page only */
  }
}

let memoryToken = "";

/** The guest token, creating a guest on first use. */
export async function ensureGuest(): Promise<string> {
  const existing = memoryToken || readToken();
  if (existing) {
    const check = await fetch(`${HTTP_BASE}/api/me`, {
      headers: { Authorization: `Bearer ${existing}` },
    });
    if (check.ok) {
      memoryToken = existing;
      return existing;
    }
  }
  const response = await fetch(`${HTTP_BASE}/api/guest`, { method: "POST" });
  if (!response.ok) throw new ApiError(response.status, await safeJson(response));
  const body = await response.json();
  memoryToken = String(body.token);
  writeToken(memoryToken);
  return memoryToken;
}

export function currentToken(): string {
  return memoryToken || readToken();
}

export function forgetGuest() {
  memoryToken = "";
  writeToken("");
}

async function safeJson(response: Response): Promise<Record<string, unknown>> {
  try {
    return await response.json();
  } catch {
    return { error: `Request failed (${response.status})` };
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await ensureGuest();
  const response = await fetch(`${HTTP_BASE}${path}`, {
    ...init,
    headers: { ...(init.headers ?? {}), Authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw new ApiError(response.status, await safeJson(response));
  return (await response.json()) as T;
}

/** Download an authenticated file (transcript or scorecard). */
export async function download(path: string, filename: string) {
  const token = await ensureGuest();
  const response = await fetch(`${HTTP_BASE}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw new ApiError(response.status, await safeJson(response));
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Types mirrored from the server
// ---------------------------------------------------------------------------

export type RoleFamily =
  | "software"
  | "hardware"
  | "sales"
  | "marketing"
  | "operations"
  | "finance"
  | "generic";
export type Seniority = "junior" | "mid" | "senior" | "lead";
export type RoundChoice = "hr" | "hiring_manager" | "domain_specialist" | "full";
export type Lane = "voice" | "text";
export type Intensity = "coach" | "realistic" | "panel";

export type Evidence = { source?: string; excerpt?: string; turn_id?: string; quote?: string; session_id?: string };

export type PrepItem = {
  id: string;
  kind: string;
  title: string;
  detail: string;
  action: string;
  evidence: Evidence[];
};

export type IntakeClaim = {
  id: string;
  text: string;
  competency: string;
  source: string;
  source_path: string;
  evidence_kind: string;
};

export type IntakeStatus = {
  state: string;
  stage_index?: number;
  stage_count?: number;
  error?: string;
  recovery?: string;
  warnings?: string[];
  claim_count?: number;
};

export type IntakeResult = {
  status: IntakeStatus;
  config?: Record<string, unknown>;
  claims?: IntakeClaim[];
  fit_gap?: { matched: string[]; missing: string[]; unsure: string[]; summary: string; required: string[] };
  prep?: { items: PrepItem[] };
  sources?: Record<string, any>;
  coverage_note?: string;
  evidence_note?: string;
};

export type Citation = { turn_id: string; quote: string; verified: boolean };

export type DimensionResult = {
  dimension_id: string;
  label: string;
  kind: string;
  weight: number;
  level: "insufficient_evidence" | "developing" | "solid" | "strong";
  assessed: boolean;
  score: number | null;
  rationale: string;
  citations: Citation[];
};

export type ReportFinding = {
  perspective: string;
  perspective_label: string;
  round: string;
  dimension_id: string;
  dimension_label: string;
  polarity: "strength" | "gap";
  explanation: string;
  quote: string;
  turn_id: string;
  t_start: number | null;
  confidence: "high" | "moderate" | "low";
  practice: string;
};

export type RoundResult = {
  round: string;
  label: string;
  perspective: string;
  perspective_label: string;
  pack_id: string;
  rubric_version: string;
  status: "evaluated" | "not_reached" | "no_answers";
  coverage: Record<string, any>;
  candidate_turns: number;
  dimensions: DimensionResult[];
  aggregate: {
    score: number | null;
    assessed_dimensions: string[];
    excluded_dimensions: string[];
    weights_used: Record<string, number>;
    note: string;
    disclaimer: string;
  };
  findings: ReportFinding[];
  rejected_evidence: number;
};

export type ClaimPosition = {
  perspective: string;
  status: string;
  reason: string;
  quote: string;
  turn_id: string;
  verified: boolean;
};

export type ClaimFinding = {
  claim_id: string;
  text: string;
  source: string;
  evidence_kind: string;
  status: "held" | "collapsed" | "untested";
  meaning: string;
  reason: string;
  quote: string;
  turn_id: string;
  positions: ClaimPosition[];
};

export type Recommendation = {
  title: string;
  why: string;
  action: string;
  source: string;
  evidence: Evidence[];
  session_id?: string;
};

export type SessionReport = {
  schema_version: string;
  session_id: string;
  intake_id: string | null;
  created_at: string;
  session_started_at: string;
  config: Record<string, string>;
  lane: Lane;
  ended_reason: string;
  personalised: boolean;
  coverage_note: string;
  evidence_note: string;
  rounds: RoundResult[];
  overall: { score: number | null; rounds_scored: string[]; weights_used: Record<string, number>; note: string; disclaimer: string };
  claims: ClaimFinding[];
  claim_status_meanings: Record<string, string>;
  disagreements: { subject: string; summary: string; positions: ClaimPosition[] }[];
  delivery: {
    lane: Lane;
    assessed: boolean;
    included_in_score: boolean;
    note: string;
    measured: string[];
    not_measured: string[];
    observations: string[];
  };
  recommendations: Recommendation[];
  limitations: string[];
  evaluator: { provider: string; model: string; is_assessment: boolean };
  candidate_turns: number;
};

export type SessionMeta = {
  session_id: string;
  state: string;
  created_at: string;
  lane: Lane;
  intensity: string;
  config: Record<string, string>;
  ended_reason?: string;
  error?: string | null;
  recovery?: string | null;
  evaluation_attempts?: number;
  personalised?: boolean;
};

export type HistoryRow = {
  session_id: string;
  created_at: string;
  state: string;
  target_role: string;
  role_family: string;
  round: string;
  round_label: string;
  lane: Lane;
  intensity: string;
  ended_reason: string;
  overall_score: number | null;
  evaluator_provider: string | null;
};

export type Comparison = {
  label: string;
  sessions: number;
  kind: "comparison" | "trend";
  previous_session_id: string;
  latest_session_id: string;
  overall_previous: number | null;
  overall_latest: number | null;
  changes: { round: string; label: string; previous_level: string; latest_level: string; delta: number }[];
  series: { session_id: string; created_at: string; overall_score: number | null; intensity: string }[];
  notes: string[];
};

export type History = {
  sessions: HistoryRow[];
  comparisons: Comparison[];
  recurring_gaps: { dimension_label: string; round: string; sessions: string[] }[];
};

export type Plan = {
  items: Recommendation[];
  preparation: Recommendation[];
  based_on_sessions: number;
  note: string;
};

export const ROLE_FAMILIES: { label: string; value: RoleFamily }[] = [
  { label: "Software engineering", value: "software" },
  { label: "Hardware engineering", value: "hardware" },
  { label: "Sales", value: "sales" },
  { label: "Marketing", value: "marketing" },
  { label: "Operations", value: "operations" },
  { label: "Finance", value: "finance" },
  { label: "Other (general questions, no specialist pack)", value: "generic" },
];

export const SENIORITIES: { label: string; value: Seniority }[] = [
  { label: "Junior", value: "junior" },
  { label: "Mid-level", value: "mid" },
  { label: "Senior", value: "senior" },
  { label: "Lead / manager", value: "lead" },
];

export const ROUNDS: { label: string; value: RoundChoice; hint: string }[] = [
  { label: "Full interview", value: "full", hint: "HR, then hiring manager, then domain specialist — about 30 minutes." },
  { label: "HR", value: "hr", hint: "Background, motivation and understanding of the role." },
  { label: "Hiring manager", value: "hiring_manager", hint: "Ownership, judgement, collaboration and outcomes." },
  { label: "Domain specialist", value: "domain_specialist", hint: "Profession-specific reasoning, tradeoffs and verification." },
];

export function percent(score: number | null | undefined): string {
  return score == null ? "—" : `${Math.round(score * 100)}`;
}

export function levelLabel(level: string): string {
  return level === "insufficient_evidence" ? "Not assessed" : level[0].toUpperCase() + level.slice(1);
}
