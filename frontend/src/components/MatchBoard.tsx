import { useEffect, useState } from "react";

import { useFacets } from "../hooks/useOffers";
import { fetchMatchBoard, type MatchBoard as Board, type MatchBoardRow } from "../lib/api";
import { bigMoney, money } from "../lib/money";

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
export function MatchBoard({ onOpen }: { onOpen: (row: MatchBoardRow) => void }) {
  const [board, setBoard] = useState<Board | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Which slice of demand to rank. Everything by default, because the morning question
  // is "where is the money" rather than "where is the money in phones" — but a trader
  // working one product type cannot get there otherwise, since the highest-margin rows
  // crowd every other category off the top of the screen.
  const [brand, setBrand] = useState("");
  const [category, setCategory] = useState("");

  // Facets come from the buy side: these narrow requirements, so offering a brand
  // nobody has asked for would be a filter that can only ever return nothing.
  const { brands, categories } = useFacets("buy");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    void (async () => {
      try {
        const next = await fetchMatchBoard({ brand, category });
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
  }, [brand, category]);

  if (error) return <p className="error">{error}</p>;

  const filters = (
    <div className="filters">
      <select value={brand} onChange={(e) => setBrand(e.target.value)}>
        <option value="">All brands</option>
        {brands.map((b) => (
          <option key={b} value={b}>
            {b}
          </option>
        ))}
      </select>

      <select value={category} onChange={(e) => setCategory(e.target.value)}>
        <option value="">All product types</option>
        {categories.map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </select>

      {(brand || category) && (
        <button
          className="link"
          onClick={() => {
            setBrand("");
            setCategory("");
          }}
        >
          show everything
        </button>
      )}

      {board && (
        <span className="count">{board.rows.length.toLocaleString()} requirements</span>
      )}
    </div>
  );

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
          {brand || category
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
          <Row key={row.offer_id} row={row} onOpen={onOpen} />
        ))}
      </div>
    </>
  );
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
function Row({ row, onOpen }: { row: MatchBoardRow; onOpen: (row: MatchBoardRow) => void }) {
  const parts = row.spec_parts ?? (row.spec ? row.spec.split(" · ") : []);
  const legs = row.best_legs ?? [];
  const wanted = row.quantity ?? 0;
  const filledShare =
    wanted > 0 ? Math.min(100, Math.round((row.filled_quantity / wanted) * 100)) : 0;
  const costCurrency = row.best_currency ?? row.currency;
  const fillable = row.state === "filled" || row.state === "short";

  return (
    <button className={`match-row ${row.state}`} onClick={() => onOpen(row)}>
      <div className="mr-main">
        <div className="mr-title">{row.description}</div>
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
