import { useMemo, useState } from "react";

import { money } from "../lib/money";
import { freshness, relativeAge, type Offer } from "../lib/types";

type SortKey = "unit_price" | "quantity" | "last_confirmed_at" | "description";

const SORTS: { key: SortKey; label: string; ascending: boolean }[] = [
  { key: "unit_price", label: "cheapest first", ascending: true },
  { key: "last_confirmed_at", label: "newest first", ascending: false },
  { key: "quantity", label: "biggest lot first", ascending: false },
  { key: "description", label: "by name", ascending: true },
];

interface Props {
  offers: Offer[];
  onSelect: (offer: Offer) => void;
  onMatch: (offer: Offer) => void;
  supplierName?: (id: string) => string | null;
}

/**
 * The board.
 *
 * One row is one lot, and a trader reads it top to bottom in a fixed order: what it is,
 * how it is specified, who has it, how many, what it costs, and how long ago they said
 * so. Spreading those across eight columns made the eye travel; they are grouped into
 * four cells here so a row scans in one pass.
 *
 * Freshness carries colour because it is the only column that changes meaning by the
 * hour. Suppliers send a complete list daily, so anything not confirmed today needs a
 * phone call before it is quoted — and an old row is dimmed rather than hidden, because
 * it is still the price history.
 */
export function OfferTable({ offers, onSelect, onMatch, supplierName }: Props) {
  const [sort, setSort] = useState<SortKey>("unit_price");

  const sorted = useMemo(() => {
    const chosen = SORTS.find((s) => s.key === sort) ?? SORTS[0];
    const rows = [...offers];

    rows.sort((a, b) => {
      const left = a[chosen.key];
      const right = b[chosen.key];

      // Rows missing the sort field always sink, whichever direction is chosen. An
      // offer with no price is not "the cheapest".
      if (left === null || left === undefined) return 1;
      if (right === null || right === undefined) return -1;

      const comparison =
        typeof left === "number" && typeof right === "number"
          ? left - right
          : String(left).localeCompare(String(right));

      return chosen.ascending ? comparison : -comparison;
    });

    return rows;
  }, [offers, sort]);

  if (offers.length === 0) {
    return <p className="empty">Nothing matches those filters.</p>;
  }

  return (
    <>
      <div className="sortbar">
        <span className="muted">Sort:</span>
        <select value={sort} onChange={(event) => setSort(event.target.value as SortKey)}>
          {SORTS.map((option) => (
            <option key={option.key} value={option.key}>
              {option.label}
            </option>
          ))}
        </select>
      </div>

      <table className="offers board">
        <thead>
          <tr>
            <th>Item</th>
            <th className="num">Qty</th>
            <th className="num">Price</th>
            <th>Received</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {sorted.map((offer) => {
            const age = freshness(offer.last_confirmed_at);
            const closed = offer.status !== "live";

            // Capacity · colour · grade · region. Grade stays empty until the client
            // supplies their ranked scale — an invented condition on goods worth
            // hundreds a unit is worse than a gap.
            const spec = [
              offer.capacity_gb ? `${offer.capacity_gb}GB` : null,
              offer.colour,
              offer.grade,
              offer.region_code,
            ].filter(Boolean);

            const who = supplierName?.(offer.counterparty_id) ?? null;

            return (
              <tr key={offer.id} className={[closed ? "closed" : "", age].join(" ").trim()}>
                <td className="item">
                  <div className="item-name" title={offer.description ?? ""}>
                    {offer.description ?? "—"}
                    {offer.in_conversation && <span className="tag">in conversation</span>}
                    {closed && <span className="tag closed">{offer.status}</span>}
                  </div>
                  {spec.length > 0 && <div className="item-spec">{spec.join(" · ")}</div>}
                  {who && <div className="item-who">{who}</div>}
                </td>

                <td className="num">{offer.quantity?.toLocaleString() ?? "—"}</td>

                <td className="num">
                  <div className="price">
                    {money(offer.unit_price, offer.currency)}
                  </div>
                  {/* EXW at the factory gate and DDP delivered are not the same number
                      for the same handset. Printing one price for both compares a
                      cost against a landed cost. */}
                  {offer.incoterm && <div className="incoterm">{offer.incoterm}</div>}
                </td>

                <td>
                  <span className={`age ${age}`} title={offer.last_confirmed_at}>
                    {relativeAge(offer.last_confirmed_at)}
                  </span>
                </td>

                <td className="actions">
                  <button className="link" onClick={() => onMatch(offer)}>
                    {offer.side === "buy" ? "fill this" : "place this"}
                  </button>
                  {offer.source_email_id && (
                    <button className="link" onClick={() => onSelect(offer)}>
                      email
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
  );
}
