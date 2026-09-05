import { useEffect, useState } from "react";

import { closeDeal, fetchDeals, patchDeal, type Deal, type DealLeg } from "../lib/api";
import { money } from "../lib/money";

/**
 * Deals in progress.
 *
 * The spec's shape, deliberately: buying from on top, selling to beneath, margin at the
 * foot, and a free-text status box that is the first thing on the card. There is no
 * pipeline stage anywhere — an explicit client decision, on the grounds that a pipeline
 * he does not maintain is worse than none, and what he will actually keep current is a
 * sentence like "Yusuf confirmed all 120 held until Thursday".
 *
 * A deal references line items; it never removes them from Sellers or Buyers. Nothing on
 * this screen deletes anything from the order book.
 */
export function Deals() {
  const [deals, setDeals] = useState<Deal[]>([]);
  const [openCount, setOpenCount] = useState(0);
  const [committed, setCommitted] = useState<number | null>(null);
  const [currency, setCurrency] = useState<string | null>(null);
  const [showClosed, setShowClosed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load(closed = showClosed) {
    setLoading(true);
    try {
      const data = await fetchDeals(closed);
      setDeals(data.items);
      setOpenCount(data.open_count);
      setCommitted(data.committed_value);
      setCurrency(data.currency);
      setError(null);
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : String(problem));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showClosed]);

  if (loading) return <p className="muted">Loading deals…</p>;
  if (error) return <p className="error">{error}</p>;

  return (
    <>
      <div className="tiles">
        <Tile label="Open deals" value={String(openCount)} />
        <Tile
          label="Committed to buy"
          value={committed === null ? "—" : money(committed, currency, { decimals: false })}
          hint="What the open deals have promised to suppliers. Only the buy side — summing both would count the same trade twice."
        />
        <Tile
          label="Needing a nudge"
          value={String(deals.filter((d) => d.untouched_days !== null).length)}
          tone="warn"
          hint="Open and nobody has updated the status in over a week"
        />
      </div>

      <div className="pills">
        <button
          className={showClosed ? "pill" : "pill on"}
          onClick={() => setShowClosed(false)}
        >
          Open
        </button>
        <button
          className={showClosed ? "pill on" : "pill"}
          onClick={() => setShowClosed(true)}
        >
          Everything
        </button>
      </div>

      {deals.length === 0 ? (
        <p className="empty">
          No deals yet. Open one from a match when a conversation starts.
        </p>
      ) : (
        deals.map((deal) => <DealCard key={deal.id} deal={deal} onChange={load} />)
      )}
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

function DealCard({ deal, onChange }: { deal: Deal; onChange: () => void }) {
  const [status, setStatus] = useState(deal.status);
  const [saving, setSaving] = useState(false);
  const [closing, setClosing] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const dirty = status !== deal.status;

  async function run(work: () => Promise<unknown>) {
    setSaving(true);
    setProblem(null);
    try {
      await work();
      onChange();
    } catch (failure) {
      setProblem(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setSaving(false);
    }
  }

  const title =
    deal.title ||
    deal.buying[0]?.description ||
    deal.selling[0]?.description ||
    "Untitled deal";

  return (
    <div className={`deal ${deal.closed_at ? "done" : ""}`}>
      <div className="deal-head">
        <div>
          <div className="deal-title">{title}</div>
          <div className="muted small">
            Deal #{deal.reference}
            {deal.opened_at && ` · opened ${new Date(deal.opened_at).toLocaleDateString()}`}
            {deal.closed_at && ` · closed ${deal.outcome}`}
            {/* A nudge, not a stage. The only prompt in the whole screen. */}
            {deal.untouched_days !== null && (
              <span className="reason"> no update in {deal.untouched_days} days</span>
            )}
          </div>
        </div>

        {!deal.closed_at && (
          <button className="link" onClick={() => setClosing(!closing)}>
            {closing ? "cancel" : "close deal"}
          </button>
        )}
      </div>

      {closing && <CloseForm deal={deal} onDone={onChange} />}

      {/* First thing on the card, because it is the thing that goes out of date and the
          only field the trader actually maintains. */}
      <label className="deal-status">
        <span className="deal-label">Status</span>
        <textarea
          rows={2}
          value={status}
          placeholder="What is actually happening — who confirmed what, and what you are waiting on."
          onChange={(event) => setStatus(event.target.value)}
          disabled={saving || !!deal.closed_at}
        />
      </label>

      {dirty && (
        <button
          className="resolve"
          disabled={saving}
          onClick={() => void run(() => patchDeal(deal.id, { status }))}
        >
          {saving ? "Saving…" : "Save status"}
        </button>
      )}

      <Legs title="Buying from" legs={deal.buying} />
      <Legs title="Selling to" legs={deal.selling} />

      <div className="deal-foot">
        <span className="deal-label">Margin</span>
        {/* One-sided deals show nothing rather than a revenue figure dressed as profit.
            Negotiating with a seller before a buyer exists is explicitly supported. */}
        <strong className={deal.margin === null ? "muted" : "margin"}>
          {deal.margin === null
            ? deal.buying.length && deal.selling.length
              ? "unknown — a price is missing"
              : "one side only"
            : money(deal.margin, deal.currency)}
        </strong>
      </div>

      {!deal.closed_at && (
        <label className="checkbox deal-visible">
          <input
            type="checkbox"
            checked={deal.keep_offers_visible}
            disabled={saving}
            onChange={(event) =>
              void run(() =>
                patchDeal(deal.id, { keep_offers_visible: event.target.checked }),
              )
            }
          />
          <span>
            Keep linked lots visible to the team
            <em className="muted small">
              {" "}
              — on: they stay in Sellers marked in play. Off: hidden while this deal is
              open.
            </em>
          </span>
        </label>
      )}

      {deal.loss_reason && <p className="note">Lost: {deal.loss_reason}</p>}
      {problem && <p className="error small">{problem}</p>}
    </div>
  );
}

function Legs({ title, legs }: { title: string; legs: DealLeg[] }) {
  if (legs.length === 0) return null;

  return (
    <>
      <h3>{title}</h3>
      <table className="legs">
        <tbody>
          {legs.map((leg) => (
            <tr key={leg.id}>
              <td className="who">{leg.counterparty_name ?? leg.counterparty_id}</td>
              <td className="desc" title={leg.description}>
                {leg.description}
                {leg.note && <div className="muted small">{leg.note}</div>}
              </td>
              <td className="num">{leg.quantity}</td>
              <td className="num price">{money(leg.unit_price, leg.currency)}</td>
              <td className="num price">{money(leg.value, leg.currency)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function CloseForm({ deal, onDone }: { deal: Deal; onDone: () => void }) {
  const [outcome, setOutcome] = useState<"won" | "lost">("won");
  const [quoted, setQuoted] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);

  return (
    <div className="deal-close">
      <div className="pills">
        <button
          className={outcome === "won" ? "pill on" : "pill"}
          onClick={() => setOutcome("won")}
        >
          Won
        </button>
        <button
          className={outcome === "lost" ? "pill on" : "pill"}
          onClick={() => setOutcome("lost")}
        >
          Lost
        </button>
      </div>

      <label className="review-field">
        <span>What we quoted, per unit</span>
        <input
          type="number"
          step="0.01"
          value={quoted}
          onChange={(event) => setQuoted(event.target.value)}
        />
      </label>

      {/* Only asked for on a loss, and never required. A lost deal with no reason is
          still worth recording; a trader blocked by a text box will stop closing them. */}
      {outcome === "lost" && (
        <label className="review-field">
          <span>Why it went away</span>
          <input
            value={reason}
            placeholder="Undercut on price, buyer went quiet, stock gone…"
            onChange={(event) => setReason(event.target.value)}
          />
        </label>
      )}

      <button
        className="resolve"
        disabled={busy}
        onClick={() => {
          setBusy(true);
          void closeDeal(deal.id, {
            outcome,
            quoted_unit_price: quoted === "" ? undefined : Number(quoted),
            loss_reason: reason || undefined,
          })
            .then(onDone)
            .finally(() => setBusy(false));
        }}
      >
        {busy ? "Closing…" : `Close as ${outcome}`}
      </button>
    </div>
  );
}
