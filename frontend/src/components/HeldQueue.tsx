import { useEffect, useState } from "react";

import {
  fetchHeldImport,
  fetchHeldImports,
  resolveHeld,
  type HeldChange,
  type HeldDetail,
  type HeldImport,
} from "../lib/api";
import { money } from "../lib/money";
import { EmailDrawer } from "./EmailDrawer";

/**
 * A second list from one supplier on the same day, waiting for a person to say what it
 * means.
 *
 * The first list of a day is the supplier's whole stock, and the board closes anything
 * missing from it. A second list might be the same again, a full replacement, or 'the
 * 40 I forgot' — and reading the last as a whole list would close the other 450. So it
 * is held, and the three answers are offered here:
 *
 *   nothing new       confirm the stock, change nothing
 *   replace           this is their current stock: apply it, closures included
 *   apply ticked      take only the chosen rows, close nothing
 */
export function HeldQueue({ onChanged }: { onChanged?: () => void }) {
  const [items, setItems] = useState<HeldImport[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    try {
      setItems((await fetchHeldImports()).items);
      setError(null);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
    }
  }

  useEffect(() => {
    void load();
  }, []);

  // Nothing held is the normal case; the section only appears when there is a decision.
  if (error) return <p className="error small">{error}</p>;
  if (!items || items.length === 0) return null;

  return (
    <>
      <h3 className="section">Second lists today</h3>
      <p className="muted queue-summary">
        {items.length} supplier{items.length === 1 ? "" : "s"} sent another list after
        today's was applied. Nothing from {items.length === 1 ? "it" : "them"} has reached
        the board yet.
      </p>
      {items.map((item) => (
        <HeldCard
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

const KIND_LABEL: Record<HeldChange["kind"], string> = {
  appeared: "New lines",
  price: "Price changed",
  quantity: "Quantity changed",
  vanished: "Missing from this list",
  unchanged: "Unchanged",
};

function HeldCard({ item, onDone }: { item: HeldImport; onDone: () => void }) {
  const [detail, setDetail] = useState<HeldDetail | null>(null);
  const [open, setOpen] = useState(false);
  const [reading, setReading] = useState(false);
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next && !detail) {
      try {
        setDetail(await fetchHeldImport(item.id));
      } catch (failure) {
        setProblem(failure instanceof Error ? failure.message : String(failure));
      }
    }
  }

  async function answer(resolution: "same" | "new" | "partial") {
    setBusy(true);
    setProblem(null);
    try {
      const result = await resolveHeld(item.id, resolution, [...ticked]);
      setOutcome(
        resolution === "same"
          ? `Kept as it was — ${result.refreshed} lots confirmed as still current.`
          : `${result.applied} lines applied${result.closed ? `, ${result.closed} closed as sold` : ""}.`,
      );
      window.setTimeout(onDone, 1600);
    } catch (failure) {
      setProblem(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  }

  const tickable = (detail?.changes ?? []).filter((c) => c.kind !== "vanished" && c.kind !== "unchanged");

  return (
    <div className="review-card">
      <div className="review-head">
        <div>
          <div className="review-subject">{item.counterparty_name ?? "unknown sender"}</div>
          <div className="muted small">
            {item.row_count.toLocaleString()} rows · {item.side === "buy" ? "buying" : "selling"}
            {item.created_at && ` · ${new Date(item.created_at).toLocaleString()}`}
          </div>
          {item.email_id && (
            <button className="link open-email" onClick={() => setReading(true)}>
              Open the email
            </button>
          )}
        </div>
        <span className="reason check">second list today</span>
      </div>

      {item.difference_summary && <p className="small">{item.difference_summary}</p>}

      {outcome ? (
        <p className="resolved">{outcome}</p>
      ) : (
        <>
          <div className="import-actions">
            <button className="link" onClick={() => void toggle()}>
              {open ? "Hide the changes" : "See what would change"}
            </button>
          </div>

          {open && !detail && !problem && <p className="muted small">Comparing with the board…</p>}

          {open && detail && (
            <div className="held-changes">
              {(["appeared", "price", "quantity", "vanished"] as const).map((kind) => {
                const rows = detail.changes.filter((c) => c.kind === kind);
                if (rows.length === 0) return null;
                return (
                  <details key={kind} className={`held-group ${kind}`} open={rows.length <= 12}>
                    <summary>
                      {KIND_LABEL[kind]} <span className="muted">({rows.length})</span>
                    </summary>
                    <table className="held-table">
                      <tbody>
                        {rows.map((change) => (
                          <tr key={change.identity_key}>
                            <td className="held-tick">
                              {kind !== "vanished" && (
                                <input
                                  type="checkbox"
                                  aria-label={`Apply ${change.description}`}
                                  checked={ticked.has(change.identity_key)}
                                  onChange={(e) => {
                                    const next = new Set(ticked);
                                    if (e.target.checked) next.add(change.identity_key);
                                    else next.delete(change.identity_key);
                                    setTicked(next);
                                  }}
                                />
                              )}
                            </td>
                            <td className="held-desc" title={change.description}>
                              {change.description}
                            </td>
                            <td className="num">
                              {change.previous_quantity !== null && change.previous_quantity !== change.quantity && (
                                <span className="muted">{change.previous_quantity} → </span>
                              )}
                              {change.quantity ?? "—"}
                            </td>
                            <td className="num">
                              {change.previous_price !== null && change.previous_price !== change.unit_price && (
                                <span className="muted">{money(change.previous_price, change.currency)} → </span>
                              )}
                              {money(change.unit_price, change.currency)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </details>
                );
              })}
              {(detail.counts.unchanged ?? 0) > 0 && (
                <p className="muted small">{detail.counts.unchanged} lines unchanged.</p>
              )}
            </div>
          )}

          <div className="held-answers">
            <button className="resolve quiet" disabled={busy} onClick={() => void answer("same")}>
              Nothing new — keep the board
            </button>
            <button className="resolve" disabled={busy} onClick={() => void answer("new")}>
              Replace their list with this one
            </button>
            {open && tickable.length > 0 && (
              <button
                className="resolve quiet"
                disabled={busy || ticked.size === 0}
                onClick={() => void answer("partial")}
              >
                Apply {ticked.size || ""} ticked line{ticked.size === 1 ? "" : "s"} only
              </button>
            )}
          </div>
          <p className="muted small">
            Replacing closes anything on the board that this list leaves out. Applying
            ticked lines closes nothing.
          </p>
        </>
      )}

      {problem && <p className="error small">{problem}</p>}

      {reading && item.email_id && (
        <EmailDrawer
          emailId={item.email_id}
          title={item.counterparty_name}
          onClose={() => setReading(false)}
        />
      )}
    </div>
  );
}
