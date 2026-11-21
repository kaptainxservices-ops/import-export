import { useEffect, useRef, useState } from "react";

import { useFacets, type ClientOption } from "../hooks/useOffers";
import {
  fetchMatchBoard,
  type MatchBoard as Board,
  type MatchBoardRow,
  type MatchSort,
} from "../lib/api";
import { bigMoney, money } from "../lib/money";
import { boardSignature, fetchStoredBoard, NoStoredBoard } from "../lib/boardMatches";
import { supabase } from "../lib/supabase";
import { relativeAge } from "../lib/types";

interface Props {
  onOpen: (row: MatchBoardRow) => void;
  /** Open one buyer's products and matches. */
  onBuyer?: (buyerId: string) => void;
  /** Counterparties with live requirements, and with live lots. */
  buyers: ClientOption[];
  suppliers: ClientOption[];
  sort: MatchSort;
  onSortChange: (sort: MatchSort) => void;
  /** Requirements that turned fillable since this person last looked: tagged NEW. */
  fresh: Set<string>;
  /** Bumped when something outside this screen should make it reload. */
  refreshKey: number;
}

/**
 * Every buyer requirement, matched against every supplier, ranked by money.
 *
 * The drawer answers "can I fill *this* one?". This answers the question a trader
 * actually opens the laptop with — "where is there money today?" — and that has to be
 * ranked across the whole board at once rather than discovered by clicking rows.
 *
 * Requirements nobody can fill stay on the list rather than being hidden. An unfillable
 * requirement is not noise, it is a thing to go and source; dropping it would hide the
 * demand along with the problem.
 */
export function MatchBoard({
  onOpen,
  onBuyer,
  buyers,
  suppliers,
  sort,
  onSortChange,
  fresh,
  refreshKey,
}: Props) {
  const [board, setBoard] = useState<Board | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Which slice of demand to rank. Everything by default, because the morning question
  // is "where is the money" rather than "where is the money in phones" — but a trader
  // working one product type cannot get there otherwise, since the highest-margin rows
  // crowd every other category off the top of the screen.
  const [brand, setBrand] = useState("");
  const [category, setCategory] = useState("");

  // One select for both kinds of client, encoded 'buyer:<id>' or 'supplier:<id>'. A
  // buyer narrows to their requirements; a supplier to what their stock can help fill.
  const [client, setClient] = useState("");
  const [clientKind, clientId] = client ? client.split(":", 2) : ["", ""];

  // Typed into straight away, sent once typing pauses: matching the whole board on
  // every keystroke would queue a dozen requests to answer the last one.
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 350);
    return () => window.clearTimeout(timer);
  }, [search]);

  // Facets come from the buy side: these narrow requirements, so offering a brand
  // nobody has asked for would be a filter that can only ever return nothing.
  const { brands, categories } = useFacets("buy");

  // The stored board changes when the backend finishes recomputing after a list lands.
  // One email rewrites many rows, so the events are gathered into one reload.
  const [changes, setChanges] = useState(0);

  // What the board looked like at the last load. Compared against the backend's
  // current one to tell whether anything was missed.
  const signature = useRef<string | null>(null);

  useEffect(() => {
    let settle: number | undefined;
    const bump = () => {
      window.clearTimeout(settle);
      settle = window.setTimeout(() => setChanges((n) => n + 1), 1000);
    };

    // Realtime is the fast path, but not a reliable one on its own: the socket drops
    // whenever the laptop sleeps, the tab sits in the background or the network
    // blips, and events sent while it was down are never replayed. Relying on it
    // alone left the tiles frozen on old numbers until a page reload, while the
    // match alerts (which re-check on focus) went on announcing new matches. So the
    // board also asks whether anything changed -- on focus, on reconnect, and on a
    // slow timer while visible -- and reloads only when something did.
    let checking = false;
    const catchUp = async () => {
      if (checking || document.visibilityState !== "visible") return;
      checking = true;
      try {
        const now = await boardSignature();
        if (now !== null && signature.current !== null && now !== signature.current) bump();
      } finally {
        checking = false;
      }
    };

    let connectedBefore = false;
    const channel = supabase
      .channel("board-matches")
      .on("postgres_changes", { event: "*", schema: "public", table: "board_matches" }, bump)
      // The state row is touched by every refresh, including the idle ones that only
      // check for lines leaving the one-week window: compare before reloading.
      .on("postgres_changes", { event: "*", schema: "public", table: "board_match_state" }, () =>
        void catchUp(),
      )
      .subscribe((status) => {
        if (status !== "SUBSCRIBED") return;
        // A reconnect: anything that changed while the socket was down was missed.
        if (connectedBefore) void catchUp();
        connectedBefore = true;
      });

    const poll = window.setInterval(() => void catchUp(), CATCH_UP_MS);
    const onVisible = () => void catchUp();
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);

    return () => {
      window.clearTimeout(settle);
      window.clearInterval(poll);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
      void supabase.removeChannel(channel);
    };
  }, []);

  // Set once the stored board is found missing, so the fallback is not retried on every
  // keystroke. Cleared by a reload of the page.
  const live = useRef(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    const filters = {
      brand,
      category,
      q: query,
      buyer: clientKind === "buyer" ? clientId : undefined,
      supplier: clientKind === "supplier" ? clientId : undefined,
      sort,
    };

    void (async () => {
      try {
        // Read before the board, so a change landing mid-load leaves the stored
        // signature behind the backend's and the next check reloads it.
        const seen = live.current ? null : await boardSignature();
        let next: Board;
        if (live.current) {
          next = await fetchMatchBoard(filters);
        } else {
          try {
            next = await fetchStoredBoard(filters);
          } catch (problem) {
            // Until migration 0017 is run and the backend has built the board once,
            // work it out live as before rather than show nothing.
            if (!(problem instanceof NoStoredBoard)) throw problem;
            live.current = true;
            next = await fetchMatchBoard(filters);
          }
        }
        if (!cancelled) {
          setBoard(next);
          if (seen !== null) signature.current = seen;
        }
      } catch (problem) {
        if (!cancelled) {
          setError(problem instanceof Error ? problem.message : String(problem));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [brand, category, query, clientKind, clientId, sort, refreshKey, changes]);

  const narrowed = Boolean(brand || category || client || query);

  // TVD's margin per currency, largest first: the real margin where the buyer gave a
  // price, an estimate on the seller's quote where they did not.
  const opportunity = (() => {
    const totals = new Map<string | null, number>();
    let known = 0;
    let estimated = 0;
    for (const r of board?.rows ?? []) {
      if (r.state !== "filled" && r.state !== "short") continue;
      if (r.total_margin !== null) {
        totals.set(r.currency, (totals.get(r.currency) ?? 0) + r.total_margin);
        known += 1;
      } else if (r.blended_unit_cost !== null && r.filled_quantity > 0) {
        const cur = r.best_currency ?? r.currency;
        const margin = r.filled_quantity * r.blended_unit_cost * (ESTIMATED_MARGIN_PCT / 100);
        totals.set(cur, (totals.get(cur) ?? 0) + margin);
        estimated += 1;
      }
    }
    return {
      totals: [...totals.entries()].sort((a, b) => b[1] - a[1]),
      known,
      estimated,
    };
  })();

  const filters = (
    <>
      <div className="filters">
        <input
          className="search"
          type="search"
          placeholder="Search products — model, name or EAN…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Search products"
        />

        <select value={client} onChange={(e) => setClient(e.target.value)} aria-label="Client">
          <option value="">All clients</option>
          {buyers.length > 0 && (
            <optgroup label="Buyers — their requirements">
              {buyers.map((c) => (
                <option key={`b${c.id}`} value={`buyer:${c.id}`}>
                  {c.name}
                </option>
              ))}
            </optgroup>
          )}
          {suppliers.length > 0 && (
            <optgroup label="Suppliers — what they can fill">
              {suppliers.map((c) => (
                <option key={`s${c.id}`} value={`supplier:${c.id}`}>
                  {c.name}
                </option>
              ))}
            </optgroup>
          )}
        </select>

        <select value={brand} onChange={(e) => setBrand(e.target.value)} aria-label="Brand">
          <option value="">All brands</option>
          {brands.map((b) => (
            <option key={b} value={b}>
              {b}
            </option>
          ))}
        </select>

        <select
          value={category}
          onChange={(e) => setCategory(e.target.value)}
          aria-label="Product type"
        >
          <option value="">All product types</option>
          {categories.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>

        {narrowed && (
          <button
            className="link"
            onClick={() => {
              setBrand("");
              setCategory("");
              setClient("");
              setSearch("");
              setQuery("");
            }}
          >
            show everything
          </button>
        )}

        {board && (
          <span className="count">{board.rows.length.toLocaleString()} requirements</span>
        )}
      </div>

      <div className="pills" role="radiogroup" aria-label="Sort matches">
        <span className="muted">Sort:</span>
        {(
          [
            ["best", "Best matches"],
            ["new", "Newest matches"],
          ] as [MatchSort, string][]
        ).map(([key, label]) => (
          <button
            key={key}
            role="radio"
            aria-checked={sort === key}
            className={sort === key ? "pill on" : "pill"}
            onClick={() => onSortChange(key)}
          >
            {label}
          </button>
        ))}
        {loading && board && <span className="muted">updating…</span>}
      </div>
    </>
  );

  if (error) {
    return (
      <>
        {filters}
        <p className="error">{error}</p>
      </>
    );
  }

  // The filter bar stays put while a new slice loads. Replacing the whole screen with a
  // spinner on every change takes away the control the trader is currently using.
  if (!board) {
    return (
      <>
        {filters}
        {loading && <p className="muted">Matching the whole board…</p>}
      </>
    );
  }

  if (board.rows.length === 0) {
    return (
      <>
        {filters}
        <p className="empty">
          {clientKind === "supplier"
            ? "Nothing on the board that this supplier's stock can fill."
            : narrowed
              ? "Nothing is being asked for in that slice of the board."
              : "No buyer requirements on the board yet. They arrive as WTB emails are read."}
        </p>
      </>
    );
  }

  return (
    <>
      {filters}
      <div className="tiles">
        <Tile
          label="Live matches"
          value={String(board.live_matches)}
          hint="Requirements confirmed in the last 7 days that current supply can fill"
        />
        <Tile
          label="Unmet demand"
          value={String(board.unmet_demand)}
          tone="warn"
          hint="Requirements confirmed in the last 7 days that nothing can fill yet"
        />
        <Tile
          label="Opportunity"
          // TVD's margin on every fillable match. Where the buyer named a price that is
          // the real margin; where they did not -- most WTB lists -- it is estimated at
          // ESTIMATED_MARGIN_PCT on the seller's quote. One total per currency: adding
          // euros to dollars needs a rate, and the board never guesses one.
          value={
            opportunity.totals.length > 0
              ? bigMoney(opportunity.totals[0][1], opportunity.totals[0][0])
              : "—"
          }
          sub={
            opportunity.totals.length > 0
              ? [
                  ...opportunity.totals
                    .slice(1)
                    .map(([cur, amount]) => `+ ${bigMoney(amount, cur)}`),
                  opportunity.known > 0 ? `${opportunity.known} at buyer price` : null,
                  opportunity.estimated > 0
                    ? `${opportunity.estimated} est. at ${ESTIMATED_MARGIN_PCT}%`
                    : null,
                ]
                  .filter(Boolean)
                  .join(" · ")
              : undefined
          }
          tone="ok"
          hint={
            "TVD's margin on this week's fillable matches. Real margin where the buyer gave " +
            `a target price; otherwise ${ESTIMATED_MARGIN_PCT}% on the seller's quote.`
          }
        />
        <Tile
          label="Supply rows"
          value={board.supply_rows.toLocaleString()}
          hint="Seller lines confirmed in the last 7 days"
        />
      </div>

      <div className="match-rows">
        <div className="match-head" aria-hidden="true">
          <span>Requirement</span>
          <span className="num">Wants</span>
          <span>Best fill</span>
          <span className="num">Value</span>
        </div>
        {board.rows.map((row) => (
          <Row key={row.offer_id} row={row} onOpen={onOpen} onBuyer={onBuyer} isNew={fresh.has(row.offer_id)} />
        ))}
      </div>
    </>
  );
}

/** TVD's margin on a deal, as a percentage of the seller's price: used to estimate the
 * Opportunity tile where the buyer stated no price. Set VITE_ESTIMATED_MARGIN_PCT to
 * change it. */
const ESTIMATED_MARGIN_PCT = Number(import.meta.env.VITE_ESTIMATED_MARGIN_PCT ?? 5) || 5;

/** How often a visible board checks for changes realtime may have missed. One small
 * query set; the board itself is reloaded only when the answer has moved. */
const CATCH_UP_MS = 20_000;

/** relativeAge reads "6 days" for a column headed Received; here it needs the "ago". */
function ago(stamp: string): string {
  const age = relativeAge(stamp);
  return age.endsWith("days") ? `${age} ago` : age;
}

function Tile({
  label,
  value,
  tone,
  hint,
  sub,
}: {
  label: string;
  value: string;
  tone?: "ok" | "warn";
  hint?: string;
  sub?: string;
}) {
  return (
    <div className="tile" title={hint}>
      <div className="tile-label">{label}</div>
      <div className={`tile-value ${tone ?? ""}`}>{value}</div>
      {sub && <div className="tile-sub">{sub}</div>}
    </div>
  );
}

const STATE_LABEL: Record<MatchBoardRow["state"], string> = {
  filled: "Can fill",
  short: "Part fill",
  near_miss: "Near miss",
  no_supply: "No supply",
};

/**
 * One requirement, read left to right in the order a trader asks the questions: what is
 * wanted and by whom, how many, who can supply it, and what it is worth.
 *
 * Four aligned columns under one header rather than three lines of prose per row. The
 * old layout joined everything with middots — buyer, quantity, best supplier, blended
 * cost, 'buyer stated no price' — so every row was a sentence to be read, and the
 * figures never lined up from one row to the next.
 */
function Row({
  row,
  onOpen,
  onBuyer,
  isNew,
}: {
  row: MatchBoardRow;
  onOpen: (row: MatchBoardRow) => void;
  onBuyer?: (buyerId: string) => void;
  isNew: boolean;
}) {
  const parts = row.spec_parts ?? (row.spec ? row.spec.split(" · ") : []);
  const legs = row.best_legs ?? [];
  const wanted = row.quantity ?? 0;
  const filledShare =
    wanted > 0 ? Math.min(100, Math.round((row.filled_quantity / wanted) * 100)) : 0;
  const costCurrency = row.best_currency ?? row.currency;
  const fillable = row.state === "filled" || row.state === "short";

  return (
    <button className={`match-row ${row.state}${isNew ? " is-new" : ""}`} onClick={() => onOpen(row)}>
      <div className="mr-main">
        <div className="mr-title">
          {row.description}
          {isNew && <span className="tag new">NEW</span>}
        </div>
        {parts.length > 0 && (
          <div className="mr-chips">
            {parts.map((part) => (
              <span key={part} className="spec-chip">
                {part}
              </span>
            ))}
          </div>
        )}
        <div className="mr-buyer">
          {row.buyer_id && onBuyer ? (
            // A span, not a button: the whole row is already a button.
            <span
              role="button"
              tabIndex={0}
              className="link buyer-link"
              title="See everything this buyer wants and draft one email"
              onClick={(e) => {
                e.stopPropagation();
                onBuyer(row.buyer_id as string);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  e.stopPropagation();
                  onBuyer(row.buyer_id as string);
                }
              }}
            >
              {row.counterparty_name ?? "Buyer not named"}
            </span>
          ) : (
            (row.counterparty_name ?? "Buyer not named")
          )}
          {row.country && <span className="muted"> · {row.country}</span>}
        </div>
      </div>

      <div className="mr-ask">
        <div className="mr-figure">{row.quantity !== null ? row.quantity.toLocaleString() : "—"}</div>
        <div className="mr-sub">
          {row.unit_price !== null
            ? `up to ${money(row.unit_price, row.currency)}`
            : "no target price"}
        </div>
      </div>

      <div className="mr-fill">
        <div className="mr-state-line">
          <span className={`state-pill ${row.state}`}>{STATE_LABEL[row.state]}</span>
          {fillable && wanted > 0 && (
            <span className="mr-sub">
              {row.filled_quantity.toLocaleString()} of {wanted.toLocaleString()}
            </span>
          )}
          {fillable && row.matched_at && (
            <span className="mr-sub" title={`Match possible since ${row.matched_at}`}>
              · matched {ago(row.matched_at)}
            </span>
          )}
        </div>
        {fillable && wanted > 0 && (
          <div className="fill-bar" aria-hidden="true">
            <span style={{ width: `${filledShare}%` }} />
          </div>
        )}
        {fillable && legs.length > 0 ? (
          <div className="mr-legs">
            {legs.slice(0, 2).map((leg, i) => (
              <span key={i} className="leg">
                <span className="leg-name">{leg.name}</span>
                <span className="leg-qty">
                  {leg.quantity.toLocaleString()}
                  {leg.lots && leg.lots > 1 ? ` · ${leg.lots} lots` : ""}
                </span>
              </span>
            ))}
            {legs.length > 2 && <span className="mr-sub">+{legs.length - 2} more</span>}
          </div>
        ) : (
          !fillable && row.note && <div className="mr-sub">{row.note}</div>
        )}
      </div>

      <div className="mr-money">
        {/* Only a real, fillable option shows a number. A near miss relaxed something —
            a different capacity, another colour — and printing money against it would
            promise margin nobody can collect. */}
        {row.total_margin !== null ? (
          <>
            <div className="mr-margin">
              {money(row.total_margin, row.currency, { decimals: false })}
            </div>
            <div className="mr-sub">
              margin{row.margin_pct !== null ? ` · ${row.margin_pct.toFixed(1)}%` : ""}
            </div>
          </>
        ) : row.blended_unit_cost !== null ? (
          // The usual case on a real board: a WTB list says what is wanted, not what
          // will be paid, so margin is unknowable. The buy price is not — and it is the
          // half of the trade the client controls, so it is worth more than a dash.
          <>
            <div className="mr-cost">{money(row.blended_unit_cost, costCurrency)}</div>
            <div className="mr-sub">buy price per unit</div>
          </>
        ) : (
          <div className="mr-cost none">—</div>
        )}
        <div className="mr-options">
          {row.option_count > 0
            ? `${row.option_count} option${row.option_count === 1 ? "" : "s"}`
            : row.near_miss_count > 0
              ? `${row.near_miss_count} near miss${row.near_miss_count === 1 ? "" : "es"}`
              : ""}
        </div>
      </div>
    </button>
  );
}
