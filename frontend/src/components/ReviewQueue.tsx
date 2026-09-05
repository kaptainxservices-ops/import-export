import { useEffect, useState } from "react";

import { fetchReviewQueue, resolveEmail, type ReviewItem } from "../lib/api";
import { ImportQueue } from "./ImportQueue";

/**
 * The emails the pipeline declined to file.
 *
 * Every "it goes to the review queue" in the backend points here. Two things it must
 * do that a plain list would not:
 *
 * It says *why* each one stopped, because "we could not tell who sent this" and "we
 * could not tell if they are buying or selling" are answered by different people with
 * different information, and a queue that lumps them together gets worked in the wrong
 * order.
 *
 * And it reports what resolving actually did — rows extracted, offers inserted. The
 * click is only worth making if stock appears on the board, and an outcome of
 * "processed, 0 rows" is a different problem that would otherwise look like success.
 */
export function ReviewQueue({ onResolved }: { onResolved: () => void }) {
  const [items, setItems] = useState<ReviewItem[]>([]);
  const [seen, setSeen] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    try {
      const queue = await fetchReviewQueue();
      setItems(queue.items);
      setSeen(queue.emails_seen);
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

  if (loading) return <p className="muted">Loading the queue…</p>;
  if (error) return <p className="error">{error}</p>;

  // Two different jobs, in the order they should be done. A price list waiting on a
  // verdict is one click and affects hundreds of rows; an email waiting on attribution
  // is one click and affects one email.
  const emails =
    items.length === 0 ? (
      seen === 0 ? (
        <p className="empty">
          No emails have arrived yet — so there is nothing to review, and nothing on the
          board either.
        </p>
      ) : (
        <p className="empty">
          No emails waiting. All {seen} were attributed and classified without help.
        </p>
      )
    ) : (
      <>
        <p className="muted queue-summary">
          {items.length} email{items.length === 1 ? "" : "s"} waiting —{" "}
          {items.filter((i) => i.needs_sender_review).length} missing a sender,{" "}
          {items.filter((i) => i.needs_classification).length} missing a side. Nothing
          from these has reached the board.
        </p>
        {items.map((item) => (
          <ReviewCard
            key={item.id}
            item={item}
            onDone={() => {
              void load();
              onResolved();
            }}
          />
        ))}
      </>
    );

  return (
    <>
      <h3 className="section">Price lists</h3>
      <ImportQueue onChanged={onResolved} />

      <h3 className="section">Emails we could not file</h3>
      {emails}
    </>
  );
}

function ReviewCard({ item, onDone }: { item: ReviewItem; onDone: () => void }) {
  const [supplier, setSupplier] = useState(item.suggested_sender_name ?? "");
  const [side, setSide] = useState<"" | "sell" | "buy">("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const needsSupplier = item.needs_sender_review;
  const needsSide = item.needs_classification;
  const ready = (!needsSupplier || supplier.trim() !== "") && (!needsSide || side !== "");

  async function submit() {
    setBusy(true);
    setProblem(null);
    try {
      const result = await resolveEmail({
        email_id: item.id,
        counterparty_email: needsSupplier ? supplier.trim() : undefined,
        side: needsSide && side !== "" ? side : undefined,
      });

      // Deliberately not just "done". A resolution that extracts nothing is a different
      // problem from one that fills the board, and both would otherwise look identical.
      setDone(
        result.row_count === 0
          ? `Filed, but nothing could be extracted (${result.outcome}).`
          : `${result.row_count} rows read — ${result.inserted} new, ${result.updated} updated.`,
      );
      window.setTimeout(onDone, 1600);
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
          <div className="review-subject">{item.subject || "(no subject)"}</div>
          <div className="muted small">
            {item.from_email || "no sender address"} ·{" "}
            {new Date(item.received_at).toLocaleString()}
            {item.attachment_count > 0 && ` · ${item.attachment_count} attachment(s)`}
          </div>
        </div>
        <span className={`reason ${item.reason}`}>{describe(item.reason)}</span>
      </div>

      {item.body_preview && <pre className="review-preview">{item.body_preview}</pre>}

      {needsSupplier && (
        <label className="review-field">
          <span>
            Who sent this?
            {item.suggested_sender_name && (
              <em className="muted"> — the message mentions “{item.suggested_sender_name}”</em>
            )}
          </span>
          <input
            type="email"
            placeholder="sales@supplier.example"
            value={supplier}
            onChange={(event) => setSupplier(event.target.value)}
            disabled={busy || done !== null}
          />
        </label>
      )}

      {needsSide && (
        <label className="review-field">
          <span>Are they selling or buying?</span>
          <select
            value={side}
            onChange={(event) => setSide(event.target.value as "" | "sell" | "buy")}
            disabled={busy || done !== null}
          >
            <option value="">choose…</option>
            <option value="sell">Selling — this is their stock</option>
            <option value="buy">Buying — this is what they want</option>
          </select>
        </label>
      )}

      {problem && <p className="error small">{problem}</p>}
      {done && <p className="resolved small">{done}</p>}

      {!done && (
        <button className="resolve" disabled={!ready || busy} onClick={() => void submit()}>
          {busy ? "Reading the email…" : "File it and read the stock"}
        </button>
      )}
    </div>
  );
}

function describe(reason: ReviewItem["reason"]): string {
  if (reason === "both") return "no sender, no side";
  return reason === "sender" ? "no sender" : "no side";
}
