import { useEffect, useState } from "react";

import {
  approveImport,
  fetchFlaggedRows,
  fetchPendingImports,
  fixRow,
  rejectImport,
  type FlaggedRow,
  type PendingImport,
} from "../lib/api";
import { money } from "../lib/money";

/**
 * Review at the level of the list, not the row.
 *
 * The spec's reasoning, and it is worth restating because it is the whole design:
 * *nobody reviews 500 rows*. The summary is doing the real work. Twelve flagged of 487
 * is a normal morning — approve, then fix the twelve. Three hundred and forty of 392
 * means the sender changed their template and the columns misaligned, and the right
 * action is to reject the entire import in one click rather than correct 340 rows by
 * hand. Without the list level a single bad parse becomes hours of cleanup, or it
 * silently poisons the order book.
 *
 * Scoring the 45 real sample emails is what set the thresholds here: at 0.2% flagged,
 * almost every card is one click.
 */
export function ImportQueue({ onChanged }: { onChanged?: () => void }) {
  const [items, setItems] = useState<PendingImport[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    try {
      setItems((await fetchPendingImports()).items);
      setError(null);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  if (loading) return <p className="muted">Loading imports…</p>;
  if (error) return <p className="error">{error}</p>;

  if (items.length === 0) {
    return (
      <p className="empty">
        No price lists waiting. Every import so far has been read and accepted.
      </p>
    );
  }

  return (
    <>
      <p className="muted queue-summary">
        {items.length} import{items.length === 1 ? "" : "s"} waiting on a verdict.
      </p>
      {items.map((item) => (
        <ImportCard
          key={item.id}
          item={item}
          onDone={() => {
            void load();
            onChanged?.();
          }}
        />
      ))}
    </>
  );
}

function ImportCard({ item, onDone }: { item: PendingImport; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [reviewing, setReviewing] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);

  const clean = item.rows_parsed - item.flagged;

  async function run(work: () => Promise<unknown>, then?: (r: unknown) => string) {
    setBusy(true);
    setProblem(null);
    try {
      const result = await work();
      if (then) setOutcome(then(result));
      else onDone();
    } catch (failure) {
      setProblem(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="review-card">
      <div className="review-head">
        <div>
          <div className="review-subject">{item.counterparty_name ?? "unknown sender"}</div>
          <div className="muted small">
            {item.subject ?? "no subject"}
            {item.received_at && ` · ${new Date(item.received_at).toLocaleString()}`}
          </div>
        </div>
        <span className={`reason ${item.verdict}`} title={item.verdict_reason}>
          {item.verdict === "healthy"
            ? "looks healthy"
            : item.verdict === "check"
              ? "worth a look"
              : "parse suspect"}
        </span>
      </div>

      {/* The three numbers that make a 500-row list reviewable at a glance. */}
      <div className="import-counts">
        <Count label="Rows parsed" value={item.rows_parsed.toLocaleString()} />
        <Count label="Flagged" value={String(item.flagged)} tone={item.flagged ? "warn" : ""} />
        <Count
          label="Their last list"
          value={item.previous_row_count ? `${item.previous_row_count} rows` : "first one"}
        />
      </div>

      <p className="muted small">{item.verdict_reason}</p>

      {/* True of the list, not of any one row — a missing currency, product names that
          matched nothing. Fixed once on the Counterparties screen rather than 1,103
          times here. */}
      {item.parse_warnings.map((warning) => (
        <p key={warning} className="note">
          {warning}
        </p>
      ))}

      {outcome ? (
        <p className="resolved">{outcome}</p>
      ) : (
        <div className="import-actions">
          <button
            className="resolve"
            disabled={busy}
            onClick={() => void run(() => approveImport(item.id))}
          >
            {busy ? "…" : `Approve ${clean.toLocaleString()} rows`}
          </button>

          {item.flagged > 0 && (
            <button className="link" onClick={() => setReviewing(!reviewing)}>
              {reviewing ? "hide" : `review the ${item.flagged}`}
            </button>
          )}

          <button
            className="link danger"
            disabled={busy}
            onClick={() =>
              void run(
                () => rejectImport(item.id),
                (r) => {
                  const result = r as { withdrawn: number; reopened: number };
                  return `Import rejected — ${result.withdrawn} rows withdrawn, ${result.reopened} restored from their previous list.`;
                },
              )
            }
          >
            reject the whole import
          </button>
        </div>
      )}

      {reviewing && <FlaggedRows importId={item.id} />}
      {problem && <p className="error small">{problem}</p>}
    </div>
  );
}

function Count({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div>
      <div className="tile-label">{label}</div>
      <div className={`import-count ${tone ?? ""}`}>{value}</div>
    </div>
  );
}

/**
 * The flagged rows, with the fix built into the row.
 *
 * Every one says what is wrong with it. A queue that says "flagged" and nothing else is
 * a queue that gets approved blindly, which is worse than no queue at all.
 */
function FlaggedRows({ importId }: { importId: string }) {
  const [rows, setRows] = useState<FlaggedRow[] | null>(null);

  useEffect(() => {
    void fetchFlaggedRows(importId).then(setRows);
  }, [importId]);

  if (rows === null) return <p className="muted small">Loading rows…</p>;
  if (rows.length === 0) return <p className="muted small">Nothing flagged here now.</p>;

  return (
    <div className="flagged">
      {rows.map((row) => (
        <FlaggedRowCard
          key={row.id}
          row={row}
          onFixed={() => setRows((current) => (current ?? []).filter((r) => r.id !== row.id))}
        />
      ))}
    </div>
  );
}

function FlaggedRowCard({ row, onFixed }: { row: FlaggedRow; onFixed: () => void }) {
  const [description, setDescription] = useState(row.description);
  const [quantity, setQuantity] = useState(row.quantity?.toString() ?? "");
  const [price, setPrice] = useState(row.unit_price?.toString() ?? "");
  const [busy, setBusy] = useState(false);

  return (
    <div className="flagged-row">
      <div className="flagged-why">{row.review_reason}</div>

      <div className="flagged-fields">
        <label className="review-field">
          <span>Product</span>
          <input value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <label className="review-field">
          <span>Quantity</span>
          <input
            type="number"
            value={quantity}
            onChange={(e) => setQuantity(e.target.value)}
          />
        </label>
        <label className="review-field">
          <span>Price {row.currency && `(${row.currency})`}</span>
          <input type="number" step="0.01" value={price} onChange={(e) => setPrice(e.target.value)} />
        </label>
      </div>

      <div className="muted small">
        {row.counterparty_name} · {row.source_ref ?? "no source line"} ·{" "}
        {money(row.unit_price, row.currency)} as read
      </div>

      <button
        className="resolve"
        disabled={busy}
        onClick={() => {
          setBusy(true);
          void fixRow(row.id, {
            description,
            quantity: quantity === "" ? undefined : Number(quantity),
            unit_price: price === "" ? undefined : Number(price),
          })
            .then(onFixed)
            .finally(() => setBusy(false));
        }}
      >
        {busy ? "Saving…" : "Fix and publish"}
      </button>
    </div>
  );
}
