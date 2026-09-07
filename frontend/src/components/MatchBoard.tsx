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

function Row({ row, onOpen }: { row: MatchBoardRow; onOpen: (row: MatchBoardRow) => void }) {
  const wants =
    row.quantity !== null && row.unit_price !== null
      ? `wants ${row.quantity} at up to ${money(row.unit_price, row.currency)}`
      : row.quantity !== null
        ? `wants ${row.quantity}`
        : "quantity not stated";

  return (
    <button className={`match-row ${row.state}`} onClick={() => onOpen(row)}>
      <div className="match-main">
        <div className="match-title">
          {row.description}
          {row.spec && <span className="match-spec"> · {row.spec}</span>}
        </div>

        <div className="match-who">
          {row.counterparty_name ?? "unknown buyer"}
          {row.country && ` · ${row.country}`} · {wants}
        </div>

        {row.best_summary ? (
          <div className="match-best">
            <strong>Best:</strong> {row.best_summary}
            {row.blended_unit_cost !== null &&
              ` · blended ${money(row.blended_unit_cost, row.currency)}`}
            {row.note && <span className="match-note"> · {row.note}</span>}
          </div>
        ) : (
          <div className="match-best muted">{row.note}</div>
        )}
      </div>

      <div className="match-money">
        {/* Only a real, fillable option shows a number. A near miss relaxed something —
            a different capacity, another colour — and printing money against it would
            promise margin nobody can collect. */}
        {row.total_margin !== null ? (
          <>
            <div className="match-margin">
              {money(row.total_margin, row.currency, { decimals: false })}
            </div>
            <div className="match-options">
              {row.option_count} option{row.option_count === 1 ? "" : "s"}
            </div>
          </>
        ) : row.blended_unit_cost !== null ? (
          // The usual case on a real board: a WTB list says what is wanted, not what
          // will be paid, so margin is unknowable. The buy price is not — and it is the
          // half of the trade the client controls, so it is worth more than a dash.
          <>
            <div className="match-cost">
              buy at {money(row.blended_unit_cost, row.currency)}
            </div>
            <div className="match-options">
              {row.filled_quantity} of {row.quantity ?? "?"} · {row.option_count} option
              {row.option_count === 1 ? "" : "s"}
            </div>
          </>
        ) : (
          <>
            <div className="match-margin none">—</div>
            <div className="match-options">
              {row.near_miss_count > 0
                ? `${row.near_miss_count} near-miss${row.near_miss_count === 1 ? "" : "es"}`
                : "no supply"}
            </div>
          </>
        )}
      </div>
    </button>
  );
}
