import { useEffect, useMemo, useState } from "react";

import type { ClientOption } from "../hooks/useOffers";
import { fetchStoredRows, type StoredMatch } from "../lib/boardMatches";
import { money } from "../lib/money";

/**
 * The Buyers or Sellers tab as a list of counterparties rather than rows.
 *
 * Who is worth calling first: how many matches each buyer's requirements have (or how
 * many buyer requirements each seller's stock can fill), and what those matches are
 * worth. Value is the real margin where the buyer named a price, and cost times the
 * client's general margin where the buyer asked to be quoted.
 */

const MARGIN_KEY = "tvd.list.margin";

function savedMargin(): number {
  try {
    const raw = window.localStorage.getItem(MARGIN_KEY);
    const value = raw === null ? NaN : Number(raw);
    return Number.isFinite(value) && value >= 0 && value <= 100 ? value : 5;
  } catch {
    return 5;
  }
}

type SortBy = "matches" | "value";

interface Stat {
  id: string;
  name: string;
  /** Buyers: requirements on the board. Sellers: buyer requirements their stock can fill. */
  rows: number;
  /** Buyers: requirements that can be filled. Sellers: of those, fully. */
  matched: number;
  /** Buyers: every option across their requirements. Sellers: same as rows. */
  matches: number;
  nearMisses: number;
  /** Value by currency; never added across currencies for display. */
  value: Map<string, number>;
  /** Some value is an estimate at the general margin. */
  estimated: boolean;
}

const num = (v: unknown): number | null => (v === null || v === undefined ? null : Number(v));

/**
 * What one match is worth: the margin the engine computed when the buyer named a price,
 * otherwise cost times the general margin. A buyer who named a price in another currency
 * from the supplier's has no computed margin and is not guessed at.
 */
function worth(
  totalMargin: unknown,
  buyerPrice: unknown,
  cost: unknown,
  filled: number,
  margin: number,
): { value: number; estimated: boolean } | null {
  const real = num(totalMargin);
  if (real !== null) return { value: real, estimated: false };
  const unitCost = num(cost);
  if (num(buyerPrice) === null && unitCost !== null && filled > 0) {
    return { value: unitCost * filled * (margin / 100), estimated: true };
  }
  return null;
}

function add(stat: Stat, currency: string | null, value: { value: number; estimated: boolean } | null) {
  if (!value) return;
  const key = (currency ?? "").toUpperCase();
  stat.value.set(key, (stat.value.get(key) ?? 0) + value.value);
  if (value.estimated) stat.estimated = true;
}

function blank(id: string, name: string): Stat {
  return { id, name, rows: 0, matched: 0, matches: 0, nearMisses: 0, value: new Map(), estimated: false };
}

export function buyerStats(rows: StoredMatch[], margin: number): Stat[] {
  const by = new Map<string, Stat>();
  for (const row of rows) {
    if (!row.buyer_id) continue;
    const stat = by.get(row.buyer_id) ?? blank(row.buyer_id, row.counterparty_name ?? "Unnamed buyer");
    by.set(row.buyer_id, stat);
    stat.rows += 1;
    stat.matches += row.option_count ?? 0;
    stat.nearMisses += row.near_miss_count ?? 0;
    if (row.state === "filled" || row.state === "short") {
      stat.matched += 1;
      add(
        stat,
        row.best_currency ?? row.currency,
        worth(row.total_margin, row.unit_price, row.blended_unit_cost, row.filled_quantity, margin),
      );
    }
  }
  return [...by.values()];
}

export function sellerStats(rows: StoredMatch[], names: Record<string, string>, margin: number): Stat[] {
  const by = new Map<string, Stat>();
  for (const row of rows) {
    const seen = new Set<string>();
    for (const option of row.options ?? []) {
      for (const id of option.supplier_ids) {
        // A seller's best option on this requirement is the first one they are in.
        if (seen.has(id)) continue;
        seen.add(id);
        const stat = by.get(id) ?? blank(id, names[id] ?? "Unnamed seller");
        by.set(id, stat);
        stat.rows += 1;
        stat.matches += 1;
        if (!option.shortfall) stat.matched += 1;
        add(
          stat,
          option.currency,
          worth(option.total_margin, row.unit_price, option.blended_unit_cost, option.filled_quantity, margin),
        );
      }
    }
  }
  return [...by.values()];
}

function faceValue(stat: Stat): number {
  let total = 0;
  for (const v of stat.value.values()) total += v;
  return total;
}

export function ClientList({
  side,
  clients,
  names,
  active,
  onOpen,
}: {
  side: "buy" | "sell";
  /** Everyone with a live row on this side, so those with no match are listed too. */
  clients: ClientOption[];
  names: Record<string, string>;
  active: boolean;
  onOpen: (id: string) => void;
}) {
  const buyer = side === "buy";
  const [rows, setRows] = useState<StoredMatch[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState<SortBy>("matches");
  const [margin, setMargin] = useState(savedMargin);
  const [onlyMatched, setOnlyMatched] = useState(true);
  const [query, setQuery] = useState("");

  useEffect(() => {
    if (!active || rows) return;
    fetchStoredRows()
      .then(setRows)
      .catch((e: Error) => setError(e.message));
  }, [active, rows]);

  function changeMargin(value: number) {
    const v = Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0;
    setMargin(v);
    try {
      window.localStorage.setItem(MARGIN_KEY, String(v));
    } catch {
      // fine
    }
  }

  const stats = useMemo(() => {
    if (!rows) return [];
    const found = buyer ? buyerStats(rows, margin) : sellerStats(rows, names, margin);
    const known = new Set(found.map((s) => s.id));
    // Everyone with a live row, matched or not.
    for (const c of clients) if (!known.has(c.id)) found.push(blank(c.id, c.name));
    for (const s of found) {
      const label = clients.find((c) => c.id === s.id)?.name;
      if (label) s.name = label;
    }
    return found;
  }, [rows, buyer, margin, names, clients]);

  const shown = useMemo(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    return stats
      .filter((s) => !onlyMatched || s.matches > 0)
      .filter((s) => words.every((w) => s.name.toLowerCase().includes(w)))
      .sort((a, b) =>
        sortBy === "matches"
          ? b.matches - a.matches || faceValue(b) - faceValue(a)
          : faceValue(b) - faceValue(a) || b.matches - a.matches,
      );
  }, [stats, onlyMatched, query, sortBy]);

  return (
    <div className="client-list">
      <div className="cl-tools">
        <input
          type="search"
          placeholder={`Search ${buyer ? "buyers" : "sellers"}…`}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="seg" role="group" aria-label="Sort">
          <button className={sortBy === "matches" ? "on" : ""} onClick={() => setSortBy("matches")}>
            Most matches
          </button>
          <button className={sortBy === "value" ? "on" : ""} onClick={() => setSortBy("value")}>
            Best value
          </button>
        </div>
        <label className="cl-margin" title="Used where the buyer asked to be quoted rather than naming a price">
          General margin
          <input
            type="number"
            min={0}
            max={100}
            step={0.5}
            value={margin}
            onChange={(e) => changeMargin(Number(e.target.value))}
          />
          %
        </label>
        <label className="cmp-toggle">
          <input type="checkbox" checked={onlyMatched} onChange={(e) => setOnlyMatched(e.target.checked)} /> Only
          with matches
        </label>
      </div>

      {error && <p className="error">{error}</p>}
      {!rows && !error && <p className="muted">Loading…</p>}

      {rows && (
        <table className="cl-table">
          <thead>
            <tr>
              <th>#</th>
              <th>{buyer ? "Buyer" : "Seller"}</th>
              {buyer ? (
                <>
                  <th className="num" title="Requirements on the board this week">Products</th>
                  <th className="num" title="Requirements something on the board can fill">Fillable</th>
                  <th className="num" title="Every way to fill them, across all their products">Matches</th>
                  <th className="num" title="Offers with a different spec">Near misses</th>
                </>
              ) : (
                <>
                  <th className="num" title="Buyer requirements this seller's stock can help fill">Matches</th>
                  <th className="num" title="Of those, filled completely by this seller">Full</th>
                </>
              )}
              <th className="num">Best value</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((s, i) => (
              <tr key={s.id} onClick={() => onOpen(s.id)} title={`Open ${s.name}`}>
                <td className="num">{i + 1}</td>
                <td>
                  <span className="link buyer-link">{s.name}</span>
                </td>
                {buyer ? (
                  <>
                    <td className="num">{s.rows}</td>
                    <td className="num">{s.matched}</td>
                    <td className="num">
                      <b>{s.matches}</b>
                    </td>
                    <td className="num muted">{s.nearMisses}</td>
                  </>
                ) : (
                  <>
                    <td className="num">
                      <b>{s.matches}</b>
                    </td>
                    <td className="num">{s.matched}</td>
                  </>
                )}
                <td className="num">
                  {s.value.size === 0 ? (
                    <span className="muted">—</span>
                  ) : (
                    [...s.value.entries()]
                      .sort((a, b) => b[1] - a[1])
                      .map(([currency, value]) => (
                        <div key={currency}>{money(value, currency || null, { decimals: false })}</div>
                      ))
                  )}
                  {s.estimated && <div className="muted small">incl. est. at {margin}%</div>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {rows && shown.length === 0 && <p className="empty">No {buyer ? "buyers" : "sellers"} to show.</p>}
      {rows && (
        <p className="muted small">
          Best value ranks by face value; amounts in different currencies are shown separately and
          never added together.
        </p>
      )}
    </div>
  );
}
