import { useEffect, useState } from "react";

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

  useEffect(() => {
    void (async () => {
      try {
        setBoard(await fetchMatchBoard());
      } catch (problem) {
        setError(problem instanceof Error ? problem.message : String(problem));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) return <p className="muted">Matching the whole board…</p>;
  if (error) return <p className="error">{error}</p>;
  if (!board) return null;

  if (board.rows.length === 0) {
    return (
      <p className="empty">
        No buyer requirements on the board yet. They arrive as WTB emails are read.
      </p>
    );
  }

  return (
    <>
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
