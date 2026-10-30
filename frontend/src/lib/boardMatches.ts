import type { MatchBoard, MatchBoardQuery, MatchBoardRow, MatchSort } from "./api";
import { supabase } from "./supabase";

/**
 * The stored match board (table `board_matches`, migration 0017).
 *
 * The backend works the board out when offers change and keeps the answer; this reads
 * it straight from Postgres like Sellers and Buyers, so filters, search and sort cost
 * a query rather than a run of the matching engine.
 */

interface Leg {
  name: string;
  quantity: number;
  lots?: number;
}

interface StoredOption {
  kind: string;
  supplier_ids: string[];
  legs: Leg[];
  summary: string | null;
  currency: string | null;
  blended_unit_cost: number | null;
  total_margin: number | null;
  margin_pct: number | null;
  shortfall: number;
  filled_quantity: number;
}

export interface StoredMatch {
  offer_id: string;
  buyer_id: string | null;
  description: string;
  spec_parts: string[];
  counterparty_name: string | null;
  country: string | null;
  quantity: number | null;
  unit_price: number | null;
  currency: string | null;
  state: MatchBoardRow["state"];
  option_count: number;
  near_miss_count: number;
  best_summary: string | null;
  best_legs: Leg[];
  best_currency: string | null;
  blended_unit_cost: number | null;
  total_margin: number | null;
  margin_pct: number | null;
  shortfall: number;
  filled_quantity: number;
  note: string | null;
  options: StoredOption[];
  supplier_ids: string[];
  matched_at: string | null;
  first_matched_at: string | null;
  last_confirmed_at: string | null;
}

const COLUMNS =
  "offer_id,buyer_id,description,spec_parts,counterparty_name,country,quantity,unit_price," +
  "currency,state,option_count,near_miss_count,best_summary,best_legs,best_currency," +
  "blended_unit_cost,total_margin,margin_pct,shortfall,filled_quantity,note,options," +
  "supplier_ids,matched_at,first_matched_at,last_confirmed_at";

const PAGE = 1000;
const MAX_ROWS = 5000;

/** Thrown when the table is not there yet: the caller falls back to the live endpoint. */
export class NoStoredBoard extends Error {}

// Postgres numeric arrives as a string through PostgREST when it does not fit a
// double exactly; the board only ever does arithmetic on these, so read them as numbers.
const num = (value: unknown): number | null =>
  value === null || value === undefined ? null : Number(value);

/** `%` and `_` typed into the search are characters, not wildcards. */
const literal = (text: string) => text.replace(/[\\%_]/g, (c) => `\\${c}`);

export async function fetchStoredBoard(filters: MatchBoardQuery): Promise<MatchBoard> {
  const { data: state, error: stateError } = await supabase
    .from("board_match_state")
    .select("supply_rows,last_full_at")
    .limit(1)
    .maybeSingle();

  // No table (migration not run), or a table the backend has never filled: either way
  // there is no stored answer yet, and an empty board would be a wrong one.
  if (stateError || !state?.last_full_at) {
    throw new NoStoredBoard(stateError?.message ?? "the stored board has not been built yet");
  }

  const rows: StoredMatch[] = [];
  for (let start = 0; start < MAX_ROWS; start += PAGE) {
    let query = supabase
      .from("board_matches")
      .select(COLUMNS)
      .order("offer_id")
      .range(start, start + PAGE - 1);

    if (filters.brand) query = query.ilike("brand", literal(filters.brand));
    if (filters.category) query = query.ilike("category", literal(filters.category));
    if (filters.buyer) query = query.eq("buyer_id", filters.buyer);
    if (filters.supplier) query = query.contains("supplier_ids", [filters.supplier]);
    for (const word of (filters.q ?? "").toLowerCase().split(/\s+/).filter(Boolean)) {
      query = query.ilike("search_text", `%${literal(word)}%`);
    }

    const { data, error } = await query;
    if (error) throw new Error(`Loading matches failed. ${error.message}`);
    rows.push(...((data ?? []) as unknown as StoredMatch[]));
    if (!data || data.length < PAGE) break;
  }

  const shown = rows.map((row) => toBoardRow(row, filters.supplier));
  rankRows(shown, filters.sort ?? "best");
  return summarise(shown, (state.supply_rows as number) ?? 0);
}

/**
 * A stored row as the board shows it.
 *
 * With a supplier chosen, the row shows the best option that supplier is part of —
 * "what can *they* fill" — not a better one from someone else. The backend stores
 * each supplier's best option for exactly this. A supplier who appears only in near
 * misses makes the row a near miss.
 */
export function toBoardRow(row: StoredMatch, supplier?: string): MatchBoardRow {
  let state = row.state;
  let best: Partial<StoredOption> & { legs?: Leg[] } = {
    summary: row.best_summary,
    legs: row.best_legs,
    currency: row.best_currency,
    blended_unit_cost: num(row.blended_unit_cost),
    total_margin: num(row.total_margin),
    margin_pct: num(row.margin_pct),
    shortfall: row.shortfall,
    filled_quantity: row.filled_quantity,
  };
  let optionCount = row.option_count;

  if (supplier) {
    const theirs = (row.options ?? []).filter((o) => o.supplier_ids.includes(supplier));
    if (theirs.length > 0) {
      const first = theirs[0];
      best = {
        ...first,
        blended_unit_cost: num(first.blended_unit_cost),
        total_margin: num(first.total_margin),
        margin_pct: num(first.margin_pct),
      };
      state = first.shortfall ? "short" : "filled";
      optionCount = theirs.length;
    } else {
      best = { summary: null, legs: [], currency: null, blended_unit_cost: null,
               total_margin: null, margin_pct: null, shortfall: 0, filled_quantity: 0 };
      state = "near_miss";
      optionCount = 0;
    }
  }

  return {
    offer_id: row.offer_id,
    description: row.description,
    spec: (row.spec_parts ?? []).join(" · "),
    spec_parts: row.spec_parts ?? [],
    counterparty_name: row.counterparty_name,
    country: row.country,
    quantity: row.quantity,
    unit_price: num(row.unit_price),
    currency: row.currency,
    state,
    option_count: optionCount,
    near_miss_count: row.near_miss_count,
    best_summary: best.summary ?? null,
    best_legs: best.legs ?? [],
    best_currency: best.currency ?? null,
    blended_unit_cost: best.blended_unit_cost ?? null,
    total_margin: best.total_margin ?? null,
    margin_pct: best.margin_pct ?? null,
    shortfall: best.shortfall ?? 0,
    filled_quantity: best.filled_quantity ?? 0,
    note: supplier && state === "near_miss" ? "near miss with this supplier" : row.note,
    buyer_id: row.buyer_id,
    supplier_ids: row.supplier_ids,
    matched_at: state === "filled" || state === "short" ? row.matched_at : null,
    last_confirmed_at: row.last_confirmed_at,
  };
}

const STATE_ORDER: Record<MatchBoardRow["state"], number> = {
  filled: 0,
  short: 1,
  near_miss: 2,
  no_supply: 3,
};

/** The same order as app/api/matches.py::rank_rows. */
export function rankRows(rows: MatchBoardRow[], sort: MatchSort) {
  rows.sort(
    (a, b) =>
      STATE_ORDER[a.state] - STATE_ORDER[b.state] ||
      (b.total_margin ?? 0) - (a.total_margin ?? 0) ||
      (b.filled_quantity ?? 0) - (a.filled_quantity ?? 0),
  );
  if (sort === "new") {
    // Stable: rows matched at the same moment keep the 'best' order.
    const when = (r: MatchBoardRow) => (r.matched_at ? Date.parse(r.matched_at) : -Infinity);
    rows.sort((a, b) => when(b) - when(a));
  }
}

function summarise(rows: MatchBoardRow[], supplyRows: number): MatchBoard {
  let live = 0;
  let opportunity = 0;
  let currency: string | null = null;
  for (const row of rows) {
    if (row.state === "filled" || row.state === "short") {
      live += 1;
      if (row.total_margin) {
        opportunity += row.total_margin;
        currency = currency ?? row.currency;
      }
    }
  }
  return {
    rows,
    live_matches: live,
    unmet_demand: rows.length - live,
    opportunity: Math.round(opportunity * 100) / 100,
    currency,
    supply_rows: supplyRows,
  };
}

/** Fillable requirements first matched after `since`, newest first. */
export async function fetchNewMatches(since: string): Promise<StoredMatch[]> {
  const { data, error } = await supabase
    .from("board_matches")
    .select(COLUMNS)
    .gt("first_matched_at", since)
    .in("state", ["filled", "short"])
    .order("first_matched_at", { ascending: false })
    .limit(200);
  if (error) throw new NoStoredBoard(error.message);
  return (data ?? []) as unknown as StoredMatch[];
}

/** The newest first_matched_at on the board, or null when nothing has ever matched. */
export async function latestMatchTime(): Promise<string | null> {
  const { data, error } = await supabase
    .from("board_matches")
    .select("first_matched_at")
    .not("first_matched_at", "is", null)
    .order("first_matched_at", { ascending: false })
    .limit(1);
  if (error) throw new NoStoredBoard(error.message);
  return (data?.[0]?.first_matched_at as string | undefined) ?? null;
}
