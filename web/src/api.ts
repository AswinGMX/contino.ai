// Typed client for the Script-to-Storyboard API.

export type Status =
  | "pending" | "generating" | "in_review" | "needs_approval"
  | "approved" | "rejected" | "stale" | "failed";
export type Quality = "low" | "medium" | "high";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

// API location. Empty in local dev (Vite proxies /api to :8000); in production set VITE_API_URL to the
// backend's public URL, e.g. https://contino-api.vercel.app (no trailing slash).
export const API_BASE = ((import.meta.env.VITE_API_URL as string | undefined) ?? "").replace(/\/+$/, "");

/** Prefix an API path (e.g. "/api/projects/x/images/y.png") with the backend URL. */
export const apiUrl = (path: string) => (API_BASE && path.startsWith("/api/") ? API_BASE + path : path);

/** The API returns image and download links as "/api/..." paths; point them at the backend. */
function absolutize<T>(value: T): T {
  if (!API_BASE) return value;
  if (typeof value === "string") return (value.startsWith("/api/") ? API_BASE + value : value) as T;
  if (Array.isArray(value)) return value.map(absolutize) as T;
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value)) out[k] = absolutize(v);
    return out as T;
  }
  return value;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const res = await fetch(apiUrl(path), { ...init, headers, credentials: "include" });
  if (!res.ok) {
    let code = "http_error";
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.error) ({ code, message } = body.error);
      else if (body?.detail) message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch { /* not JSON */ }
    throw new ApiError(res.status, code, message);
  }
  return res.status === 204 ? (undefined as T) : absolutize(await res.json());
}

const enc = encodeURIComponent;
const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

// ------------------------------------------------------------------ types

export interface Budget {
  limit_usd: number; spent_usd: number; in_flight_usd: number; remaining_usd: number;
  warn: boolean; blocked: boolean;
}
export interface Project {
  id: string; title: string; created_at: number; source_filename: string;
  parse_status: "pending" | "parsing" | "done" | "failed"; parse_error: string;
}
export interface Overview {
  project: Project;
  plan_status: Status | null;
  counts: {
    characters: number; locations: number; scenes: number; shots: number;
    library_total: number; library_approved: number; boards_approved: number; finals_approved: number;
  };
  active_jobs: number;
  budget: Budget;
  cost_usd: number;
  cover_url: string | null;
}
export interface ShotReaction { character_id: string; emotion: string }
export interface Shot {
  id: string; location_id: string; character_ids: string[]; action: string; dialogue: string;
  reactions: ShotReaction[]; framing: string; time_of_day: string; props: string[]; notes: string;
}
export interface Scene { id: string; title: string; shots: Shot[] }
export type CharacterKind = "person" | "animal" | "creature" | "group" | "effect";
export interface Character { id: string; name: string; description: string; reactions: string[]; kind?: CharacterKind | "" }
export interface Location { id: string; name: string; description: string }
export interface Plan { title: string; style: string; characters: Character[]; locations: Location[]; scenes: Scene[] }
export interface PlanView {
  status: Status; plan: Plan; approved_at: number | null; source_url: string | null;
  parse_status: Project["parse_status"]; parse_error: string;
}
export interface Version {
  n: number; image_key: string; source: "generated" | "uploaded" | "sheet"; comment: string; quality: string;
  url: string; captioned_url?: string; created_at: number;
}
export interface Issue { problem: string; fix: string; severity: "major" | "minor"; category?: string }
export interface ImageCheck { version: number; score: number; issues: Issue[]; suggested_fix: string; notes: string; error: string }
export interface LibraryItem {
  id: string; kind: "character" | "reaction" | "location"; subject_id: string; name: string; emotion: string;
  status: Status; comment: string; error: string; versions: Version[]; approved_version: number | null;
  current_url: string | null; approved_url: string | null;
  check: ImageCheck | null; checking: boolean;
}
export interface LibraryView {
  plan_approved: boolean;
  characters: { character: Character; base: LibraryItem | null; reactions: LibraryItem[]; reactions_locked: boolean }[];
  locations: { location: Location; item: LibraryItem | null }[];
}
export interface Review {
  id: string; shot_id: string; panel_version: number; scores: Record<string, number>;
  overall: number; verdict: "pass" | "fail"; notes: string; suggested_fix: string; issues: Issue[];
}
export interface PanelRow {
  shot: Shot; status: Status; error: string; stale_reason: string; version: number | null;
  url: string | null; captioned_url: string | null; versions: Version[]; review: Review | null; override: boolean;
  history: { version: number; overall: number; verdict: "pass" | "fail"; url: string | null; comment: string; review: Review }[];
}
export interface SceneBoard {
  id: string; title: string; locked_reason: string | null; panels: PanelRow[];
  board: { status: Status; version: number; url: string | null; approved_at: number | null; overrides: string[] };
}
export interface StoryboardView {
  scenes: SceneBoard[]; criteria_weights: Record<string, number>; pass_overall: number;
  pass_min: Record<string, number>; draft_quality: Quality; final_quality: Quality; typical_fix_cost_usd: number | null;
}
export interface FinalRow {
  shot: Shot; status: Status; error: string; stale_reason: string; version: number | null; url: string | null;
  panel_url: string | null; versions: Version[]; approved_version: number | null;
}
export interface FinalsView {
  scenes: { id: string; title: string; board_approved: boolean; shots: FinalRow[] }[];
  final_quality: Quality;
  typical_final_cost_usd: number | null;
}
export interface UsageCall {
  id: string; time: string; shot_id: string; step: string; model: string; input_tokens: number;
  output_tokens: number; cached_tokens: number; images: number; gpu_seconds: number; cost_usd: number;
  ok: boolean; attempt: number; error: string;
}
export interface UsageBucket { calls: number; input_tokens: number; output_tokens: number; images: number; cost_usd: number }
export interface UsageView {
  totals: {
    calls: number; failed_calls: number; input_tokens: number; output_tokens: number; cached_tokens: number;
    images: number; cost_usd: number; approved_finals: number; cost_per_approved_final_usd: number | null;
    shared_cost_usd: number;
  };
  by_step: Record<string, UsageBucket & { failed: number }>;
  by_shot: (UsageBucket & { shot_id: string; direct_cost_usd: number; shared_cost_usd: number; total_cost_usd: number })[];
  shared: (UsageBucket & { shot_id: string })[];
  calls: UsageCall[];
  budget: Budget;
}
export interface PublicConfig {
  storage_mode: string; auth_required: boolean;
  models: { image: string; text: string; vision: string };
}

// ------------------------------------------------------------------ endpoints

const P = (pid: string) => `/api/projects/${enc(pid)}`;

export const api = {
  config: () => request<PublicConfig>("/api/config"),
  budget: () => request<Budget>("/api/budget"),
  login: (token: string) => post<{ ok: boolean }>("/api/auth/login", { token }),

  projects: () => request<Overview[]>("/api/projects"),
  createProject: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<Project>("/api/projects", { method: "POST", body: fd });
  },
  overview: (pid: string) => request<Overview>(P(pid)),
  deleteProject: (pid: string) => request<void>(P(pid), { method: "DELETE" }),

  plan: (pid: string) => request<PlanView>(`${P(pid)}/plan`),
  savePlan: (pid: string, plan: Plan) => request<PlanView>(`${P(pid)}/plan`, { method: "PUT", body: JSON.stringify(plan) }),
  approvePlan: (pid: string) => post<PlanView>(`${P(pid)}/plan/approve`),
  reparse: (pid: string) => post(`${P(pid)}/plan/reparse`),

  library: (pid: string) => request<LibraryView>(`${P(pid)}/library`),
  generateItem: (pid: string, id: string, comment = "", quality?: Quality, fresh = false) =>
    post(`${P(pid)}/library/${enc(id)}/generate`, { comment, quality, fresh }),
  uploadItem: (pid: string, id: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request(`${P(pid)}/library/${enc(id)}/upload`, { method: "POST", body: fd });
  },
  checkItem: (pid: string, id: string) => post(`${P(pid)}/library/${enc(id)}/check`),
  approveItem: (pid: string, id: string) => post(`${P(pid)}/library/${enc(id)}/approve`),
  rejectItem: (pid: string, id: string, comment: string) => post(`${P(pid)}/library/${enc(id)}/reject`, { comment }),

  generateReactionSheet: (pid: string, cid: string, comment = "") =>
    post<{ queued: string[] }>(`${P(pid)}/characters/${enc(cid)}/reactions/generate`, { comment }),
  approveAllReactions: (pid: string, cid: string) =>
    post<{ approved: string[] }>(`${P(pid)}/characters/${enc(cid)}/reactions/approve`),

  storyboard: (pid: string) => request<StoryboardView>(`${P(pid)}/storyboard`),
  generateScene: (pid: string, sid: string, quality?: Quality) =>
    post<{ queued: string[] }>(`${P(pid)}/scenes/${enc(sid)}/generate`, { quality }),
  fixPanel: (pid: string, shot: string, comment: string, quality?: Quality) =>
    post(`${P(pid)}/panels/${enc(shot)}/fix`, { comment, quality }),
  improvePanel: (pid: string, shot: string) =>
    post<{ base_version: number; quality: string; fixing: string[] }>(`${P(pid)}/panels/${enc(shot)}/improve`),
  restorePanel: (pid: string, shot: string, version: number) => post(`${P(pid)}/panels/${enc(shot)}/restore`, { version }),
  acceptPanel: (pid: string, shot: string) => post(`${P(pid)}/panels/${enc(shot)}/accept`),
  reviewPanel: (pid: string, shot: string) => post(`${P(pid)}/panels/${enc(shot)}/review`),
  approveBoard: (pid: string, sid: string, overrides: string[]) =>
    post(`${P(pid)}/scenes/${enc(sid)}/approve`, { overrides }),

  finals: (pid: string) => request<FinalsView>(`${P(pid)}/finals`),
  renderFinals: (pid: string, sid: string, quality?: Quality, shotIds?: string[]) =>
    post<{ queued: string[] }>(`${P(pid)}/scenes/${enc(sid)}/finals`, { quality, shot_ids: shotIds }),
  approveFinal: (pid: string, shot: string) => post(`${P(pid)}/finals/${enc(shot)}/approve`),
  rejectFinal: (pid: string, shot: string, comment: string) => post(`${P(pid)}/finals/${enc(shot)}/reject`, { comment }),
  retryFinal: (pid: string, shot: string) => post(`${P(pid)}/finals/${enc(shot)}/retry`, {}),

  usage: (pid: string) => request<UsageView>(`${P(pid)}/usage`),
  usageCsvUrl: (pid: string) => apiUrl(`${P(pid)}/usage.csv`),
  exportOutputs: (pid: string) => post<{ path: string; written: Record<string, number> }>(`${P(pid)}/export`),
  exportZipUrl: (pid: string) => apiUrl(`${P(pid)}/export.zip`),
};

export const BUSY: Status[] = ["generating", "in_review"];
export const isBusy = (s?: Status | null) => !!s && BUSY.includes(s);
export const usd = (n: number | null | undefined, digits = 4) =>
  n == null ? "—" : `$${n.toFixed(digits)}`;
export const num = (n: number) => n.toLocaleString();
