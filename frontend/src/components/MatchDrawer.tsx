import { useEffect, useState } from "react";

import { findMatches, type Matches, type MatchOption } from "../lib/api";
import { money } from "../lib/money";
import type { Offer } from "../lib/types";

/**
 * The screen the product exists for.
 *
 * A filterable list is something a trader could build in a spreadsheet. What he cannot
 * do by hand is notice, across two hundred rows from eleven suppliers, that nobody has
 * the hundred units a buyer wants but three suppliers together do.
 */
/**
 * Only the fields the drawer reads.
 *
 * Narrower than `Offer` on purpose: the Matches board opens this from a row it matched,
 * not from a row loaded into the table, and demanding a full Offer would mean fetching
 * one just to show a heading.
 */
/**
 * The markup the client quotes at when a buyer has not named a price.
 *
 * Most buyers do not: a WTB list says what they want and asks to be quoted. That leaves
 * margin genuinely unknown, and the system must not invent the buyer's number. But the
 * *client's* number is not unknown at all — it is his decision, and it is the one half
 * of the trade he controls. So he sets the markup and the quote is arithmetic.
 *
 * Kept per browser rather than per tenant for now: it is a working preference a trader
 * changes between deals, not a fact about the business.
 */
const MARKUP_KEY = "tvd.quote.markup";

function savedMarkup(): number {
  try {
    const raw = window.localStorage.getItem(MARKUP_KEY);
    const value = raw === null ? NaN : Number(raw);
    return Number.isFinite(value) && value >= 0 && value <= 100 ? value : 3;
  } catch {
    return 3;
  }
}

export type MatchSubject = Pick<
  Offer,
  "id" | "side" | "description" | "quantity" | "unit_price" | "currency"
>;

export function MatchDrawer({
  offer,
  onClose,
  onShowOffer,
}: {
  offer: MatchSubject;
  onClose: () => void;
  onShowOffer?: (offerId: string, side: "sell" | "buy") => void;
}) {
  const [matches, setMatches] = useState<Matches | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const direction = offer.side === "buy" ? "fill" : "place";
  const [markup, setMarkup] = useState(savedMarkup);

  function changeMarkup(value: number) {
    setMarkup(value);
    try {
      window.localStorage.setItem(MARKUP_KEY, String(value));
    } catch {
      // A browser refusing storage is not a reason to stop quoting.
    }
  }

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    findMatches(offer.id, direction)
      .then((result) => !cancelled && setMatches(result))
      .catch((e: Error) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));

    return () => {
      cancelled = true;
    };
  }, [offer.id, direction]);

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer wide" onClick={(e) => e.stopPropagation()}>
        <header>
          <p className="muted small">
            {direction === "fill" ? "Filling a buyer requirement" : "Placing a lot"}
          </p>
          <button className="close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        <div className="subject-block">
          <h2>{offer.description}</h2>
          <p>
            {direction === "fill" ? "wants" : "has"}{" "}
            <strong>{offer.quantity ?? "?"} pcs</strong>
            {offer.unit_price !== null && (
              <>
                {direction === "fill" ? " at up to " : " at "}
                <strong>{money(offer.unit_price, offer.currency)}</strong>
              </>
            )}
          </p>
        </div>

        {direction === "fill" && offer.unit_price === null && (
          <div className="quote-bar">
            <span className="muted small">
              This buyer asked to be quoted rather than naming a price. Quote at cost
              plus
            </span>
            <input
              type="number"
              min={0}
              max={100}
              step={0.5}
              value={markup}
              onChange={(event) => changeMarkup(Number(event.target.value))}
            />
            <span className="muted small">%</span>
          </div>
        )}

        {loading && <p className="muted">Searching the board…</p>}
        {error && <p className="error">{error}</p>}

        {matches && matches.total_options === 0 && (
          <p className="empty">
            Nothing on the board matches this, even loosely.
          </p>
        )}

        {matches &&
          Object.entries(matches.groups).map(([section, options]) => (
            <section key={section} className="match-group">
              <h3>{section}</h3>
              {options.map((option, index) => (
                <Option
                  key={index}
                  option={option}
                  direction={matches.direction}
                  markup={markup}
                  onShowOffer={onShowOffer}
                />
              ))}
            </section>
          ))}
      </aside>
    </div>
  );
}

function Option({
  option,
  direction,
  markup,
  onShowOffer,
}: {
  option: MatchOption;
  direction: string;
  markup: number;
  onShowOffer?: (offerId: string, side: "sell" | "buy") => void;
}) {
  // Filling a requirement means every leg is a seller's lot; placing a lot means every
  // leg is a buyer's. The tab to open follows from the direction, not from the row.
  const legSide: "sell" | "buy" = direction === "fill" ? "sell" : "buy";
  return (
    <div className={`option ${option.shortfall ? "short" : ""}`}>
      <div className="option-head">
        <span className="fill">
          {option.filled_quantity}
          {option.requested_quantity ? ` of ${option.requested_quantity}` : ""} units
          {option.supplier_count > 1 && (
            <span className="tag">{option.supplier_count} suppliers</span>
          )}
          {option.shortfall > 0 && (
            <span className="tag closed">short {option.shortfall}</span>
          )}
        </span>

        <span className={option.total_margin === null ? "muted" : "margin"}>
          {option.total_margin === null
            ? "margin unknown"
            : `${money(option.total_margin)}${
                option.margin_pct !== null ? ` · ${option.margin_pct.toFixed(1)}%` : ""
              }`}
        </span>
      </div>

      <table className="legs">
        <tbody>
          {option.allocations.map((a, i) => (
            <tr key={i}>
              <td className="num">{a.quantity}</td>
              <td className="who">{a.counterparty_name ?? a.counterparty_id}</td>
              <td className="desc" title={a.description}>
                {onShowOffer ? (
                  <button
                    className="link leg-link"
                    onClick={() => onShowOffer(a.offer_id, legSide)}
                    title={`Show this lot on the ${legSide === "sell" ? "Sellers" : "Buyers"} tab`}
                  >
                    {a.description}
                  </button>
                ) : (
                  a.description
                )}
              </td>
              <td className="num price">{money(a.unit_price, a.currency)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="muted small">
        {direction === "fill" ? "blended cost" : "cost"} {money(option.blended_unit_cost)}
        {option.counterpart_unit_price !== null &&
          ` · ${direction === "fill" ? "buyer pays" : "average sale"} ${money(
            option.counterpart_unit_price,
          )}`}
      </p>

      {/* What to actually send back. The buyer's price is unknown, the cost is not, and
          the markup is the client's own decision — so this is arithmetic rather than a
          guess, and it is the line he pastes into a reply. */}
      {option.total_margin === null &&
        option.blended_unit_cost !== null &&
        option.filled_quantity > 0 && (
          <div className="quote">
            <div>
              <span className="quote-label">Quote</span>{" "}
              <strong>
                {money(
                  option.blended_unit_cost * (1 + markup / 100),
                  option.allocations[0]?.currency,
                )}
              </strong>{" "}
              <span className="muted small">per unit × {option.filled_quantity}</span>
            </div>
            <div className="quote-total">
              {money(
                option.blended_unit_cost * (1 + markup / 100) * option.filled_quantity,
              )}{" "}
              <span className="muted small">
                · earns{" "}
                {money(
                  option.blended_unit_cost * (markup / 100) * option.filled_quantity,
                )}
              </span>
            </div>
          </div>
        )}

      {/* Relaxations and warnings are shown, never hidden. A near-miss is a phone call,
          and a cross-border combination is a margin that freight may eat. */}
      {[...option.relaxed, ...option.warnings].map((note, i) => (
        <p key={i} className="note">
          {note}
        </p>
      ))}
    </div>
  );
}
