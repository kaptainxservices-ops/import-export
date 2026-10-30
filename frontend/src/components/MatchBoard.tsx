import { useEffect, useRef, useState } from "react";

import { useFacets, type ClientOption } from "../hooks/useOffers";
import {
  fetchMatchBoard,
  type MatchBoard as Board,
  type MatchBoardRow,
  type MatchSort,
} from "../lib/api";
import { bigMoney, money } from "../lib/money";
import { fetchStoredBoard, NoStoredBoard } from "../lib/boardMatches";
import { supabase } from "../lib/supabase";
import { relativeAge } from "../lib/types";

interface Props {
  onOpen: (row: MatchBoardRow) => void;
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
  useEffect(() => {
    let settle: number | undefined;
    const bump = () => {
      window.clearTimeout(settle);
      settle = window.setTimeout(() => setChanges((n) => n + 1), 1000);
    };
    const channel = supabase
      .channel("board-matches")
      .on("postgres_changes", { event: "*", schema: "public", table: "board_matches" }, bump)
      .on("postgres_changes", { event: "*", schema: "public", table: "board_match_state" }, bump)
      .subscribe();
    return () => {
      window.clearTimeout(settle);
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
        if (!cancelled) setBoard(next);
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
        <Tile label="Live matches" value={String(board.live_matches)} />
        <Tile label="Unmet demand" value={String(board.unmet_demand)} tone="warn" />
        <Tile
          label="Opportunity"
          value={bigMoney(board.opportunity, board.currency)}
          tone="ok"
          hint="Total margin on the best option for every fillable requirement"
        />
        <Tile label="Supply rows" value={board.supply_rows.toLocaleString()} />
      </div>

      <div className="match-rows">
        <div className="match-head" aria-hidden="true">
          <span>Requirement</span>
          <span className="num">Wants</span>
          <span>Best fill</span>
          <span className="num">Value</span>
        </div>
        {board.rows.map((row) => (
          <Row key={row.offer_id} row={row} onOpen={onOpen} isNew={fresh.has(row.offer_id)} />
        ))}
      </div>
    </>
  );
}

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
}: {
  label: string;
  value: string;
  tone?: "ok" | "warn";
  hint?: string;
}) {
  return (
    <div className="tile" title={hint}>
      <div className="tile-label">{label}</div>
      <div className={`tile-value ${tone ?? ""}`}>{value}</div>
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
  isNew,
}: {
  row: MatchBoardRow;
  onOpen: (row: MatchBoardRow) => void;
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
          {row.counterparty_name ?? "Buyer not named"}
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
