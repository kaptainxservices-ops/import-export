import { supabase } from "./supabase";

/**
 * The backend, for the things the browser cannot do itself.
 *
 * The board is read straight from Postgres — row-level security decides what a user
 * can see, so no API layer is needed for that. Matching is different: the rules that
 * decide what to promise a buyer live in Python, and a second copy in TypeScript would
 * drift from the first the moment either changed.
 */
const BASE = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

export interface Allocation {
  offer_id: string;
  counterparty_id: string;
  counterparty_name: string | null;
  quantity: number;
  unit_price: number;
  currency: string | null;
  description: string;
}

export interface MatchOption {
  kind: "exact" | "combination" | "partial" | "near_miss";
  allocations: Allocation[];
  requested_quantity: number | null;
  filled_quantity: number;
  shortfall: number;
  supplier_count: number;
  blended_unit_cost: number | null;
  counterpart_unit_price: number | null;
  unit_margin: number | null;
  total_margin: number | null;
  margin_pct: number | null;
  relaxed: string[];
  warnings: string[];
}

export interface Matches {
  subject_id: string;
  subject_description: string;
  subject_quantity: number | null;
  subject_price: number | null;
  subject_currency: string | null;
  direction: "fill" | "place";
  groups: Record<string, MatchOption[]>;
  total_options: number;
}

export interface ReviewItem {
  id: string;
  received_at: string;
  from_email: string;
  subject: string;
  reason: "sender" | "classification" | "both";
  needs_sender_review: boolean;
  needs_classification: boolean;
  classification: string;
  counterparty_id: string | null;
  counterparty_name: string | null;
  counterparty_method: string;
  counterparty_confidence: number;
  suggested_sender_name: string | null;
  body_preview: string;
  attachment_count: number;
}

export interface ReviewQueue {
  items: ReviewItem[];
  total: number;
  awaiting_sender: number;
  awaiting_classification: number;
  emails_seen: number;
}

export interface Resolution {
  email_id: string;
  outcome: string;
  counterparty_id: string | null;
  side: string | null;
  row_count: number;
  inserted: number;
  updated: number;
  closed: number;
  notes: string[];
}

async function authorised(
  path: string,
  body?: unknown,
  method?: "POST" | "PATCH",
): Promise<Response> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  if (!token) throw new Error("not signed in");

  // The tenant is deliberately not sent. The backend resolves it from this token, so a
  // tampered request cannot ask for another client's board.
  const headers: Record<string, string> = { Authorization: `Bearer ${token}` };
  if (body !== undefined) headers["Content-Type"] = "application/json";

  return fetch(`${BASE}${path}`, {
    method: body === undefined ? "GET" : (method ?? "POST"),
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

async function json<T>(response: Response, what: string): Promise<T> {
  if (response.ok) return (await response.json()) as T;

  const detail = await response.text();
  throw new Error(
    response.status === 401
      ? "Session expired — sign in again."
      : `${what} failed (${response.status}). ${detail.slice(0, 200)}`,
  );
}

export async function findMatches(
  offerId: string,
  direction: "fill" | "place",
): Promise<Matches> {
  const response = await authorised(
    `/matches/${direction}?offer_id=${encodeURIComponent(offerId)}`,
  );
  return json<Matches>(response, "Matching");
}

export interface Supplier {
  id: string;
  primary_email: string;
  name: string | null;
  country: string | null;
  default_currency: string | null;
  decimal_separator: "comma" | "dot" | null;
  staleness_hours: number | null;
  typical_row_count: number | null;
  live_offers: number;
  last_seen_at: string | null;
  sample_prices: string[];
}

export interface Suppliers {
  items: Supplier[];
  unconfigured: number;
  currencies: string[];
}

export interface MatchBoardRow {
  offer_id: string;
  description: string;
  spec: string;
  counterparty_name: string | null;
  country: string | null;
  quantity: number | null;
  unit_price: number | null;
  currency: string | null;
  state: "filled" | "short" | "near_miss" | "no_supply";
  option_count: number;
  near_miss_count: number;
  best_summary: string | null;
  blended_unit_cost: number | null;
  total_margin: number | null;
  margin_pct: number | null;
  shortfall: number;
  filled_quantity: number;
  note: string | null;
}

export interface MatchBoard {
  rows: MatchBoardRow[];
  live_matches: number;
  unmet_demand: number;
  opportunity: number;
  currency: string | null;
  supply_rows: number;
}

export interface DealLeg {
  id: string;
  side: "buy" | "sell";
  counterparty_id: string;
  counterparty_name: string | null;
  offer_id: string | null;
  description: string;
  quantity: number;
  unit_price: number | null;
  currency: string | null;
  value: number | null;
  note: string | null;
}

export interface Deal {
  id: string;
  reference: number;
  title: string | null;
  status: string;
  status_updated_at: string | null;
  keep_offers_visible: boolean;
  opened_at: string | null;
  closed_at: string | null;
  outcome: "won" | "lost" | null;
  quoted_unit_price: number | null;
  loss_reason: string | null;
  buying: DealLeg[];
  selling: DealLeg[];
  margin: number | null;
  currency: string | null;
  untouched_days: number | null;
}

export interface Deals {
  items: Deal[];
  open_count: number;
  committed_value: number | null;
  currency: string | null;
}

export async function fetchDeals(includeClosed = false): Promise<Deals> {
  return json<Deals>(
    await authorised(`/deals?include_closed=${includeClosed}`),
    "Loading deals",
  );
}

export async function createDeal(title: string, status: string): Promise<Deal> {
  return json<Deal>(await authorised("/deals", { title, status }), "Opening the deal");
}

export async function patchDeal(
  id: string,
  patch: { title?: string; status?: string; keep_offers_visible?: boolean },
): Promise<Deal> {
  return json<Deal>(
    await authorised(`/deals/${encodeURIComponent(id)}`, patch, "PATCH"),
    "Saving",
  );
}

export async function closeDeal(
  id: string,
  body: { outcome: "won" | "lost"; quoted_unit_price?: number; loss_reason?: string },
): Promise<Deal> {
  return json<Deal>(
    await authorised(`/deals/${encodeURIComponent(id)}/close`, body),
    "Closing the deal",
  );
}

export async function fetchMatchBoard(
  filters: { brand?: string; category?: string } = {},
): Promise<MatchBoard> {
  const query = new URLSearchParams();
  if (filters.brand) query.set("brand", filters.brand);
  if (filters.category) query.set("category", filters.category);
  const suffix = query.toString() ? `?${query}` : "";

  return json<MatchBoard>(
    await authorised(`/matches/board${suffix}`),
    "Matching the board",
  );
}

export async function fetchSuppliers(): Promise<Suppliers> {
  return json<Suppliers>(await authorised("/suppliers"), "Loading suppliers");
}

export async function updateSupplier(
  id: string,
  patch: Partial<Pick<Supplier, "default_currency" | "decimal_separator" | "staleness_hours">>,
): Promise<Supplier> {
  return json<Supplier>(
    await authorised(`/suppliers/${encodeURIComponent(id)}`, patch, "PATCH"),
    "Saving",
  );
}

export interface PendingImport {
  id: string;
  counterparty_name: string | null;
  subject: string | null;
  received_at: string | null;
  created_at: string | null;
  rows_parsed: number;
  flagged: number;
  parse_warnings: string[];
  status: string;
  previous_row_count: number | null;
  verdict: "healthy" | "check" | "suspect";
  verdict_reason: string;
}

export interface FlaggedRow {
  id: string;
  side: string;
  description: string;
  quantity: number | null;
  unit_price: number | null;
  currency: string | null;
  confidence: number | null;
  review_reason: string | null;
  source_ref: string | null;
  source_email_id: string | null;
  counterparty_name: string | null;
}

export async function fetchPendingImports(): Promise<{
  items: PendingImport[];
  waiting: number;
}> {
  return json(await authorised("/review/imports"), "Loading imports");
}

export async function approveImport(id: string): Promise<unknown> {
  return json(
    await authorised(`/review/imports/${encodeURIComponent(id)}/approve`, {}),
    "Approving",
  );
}

export async function rejectImport(
  id: string,
): Promise<{ withdrawn: number; reopened: number }> {
  return json(
    await authorised(`/review/imports/${encodeURIComponent(id)}/reject`, {}),
    "Rejecting",
  );
}

export async function fetchFlaggedRows(importId?: string): Promise<FlaggedRow[]> {
  const q = importId ? `?import_id=${encodeURIComponent(importId)}` : "";
  return json<FlaggedRow[]>(await authorised(`/review/rows${q}`), "Loading rows");
}

export async function fixRow(
  id: string,
  patch: { description?: string; quantity?: number; unit_price?: number; currency?: string },
): Promise<unknown> {
  return json(
    await authorised(`/review/rows/${encodeURIComponent(id)}`, patch, "PATCH"),
    "Saving",
  );
}

export async function fetchReviewQueue(): Promise<ReviewQueue> {
  return json<ReviewQueue>(await authorised("/review/queue"), "Loading the queue");
}

export async function resolveEmail(input: {
  email_id: string;
  counterparty_email?: string;
  counterparty_name?: string;
  side?: "sell" | "buy";
}): Promise<Resolution> {
  return json<Resolution>(await authorised("/review/resolve", input), "Resolving");
}
