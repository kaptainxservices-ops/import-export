import { useEffect, useRef, useState } from "react";

import { Filters } from "./Filters";
import { OfferTable } from "./OfferTable";
import {
  emptyFilters,
  useFacets,
  useOffers,
  type ClientOption,
  type OfferFilters,
} from "../hooks/useOffers";
import type { Offer, Side } from "../lib/types";

interface Props {
  side: Side;
  /** Whether this screen is the one showing. Hidden, it keeps its rows and does no work. */
  active: boolean;
  clients: ClientOption[];
  supplierNames: Record<string, string>;
  /** Bumped after a review resolution, which adds rows. */
  refreshKey: number;
  /** One lot to show, arrived at from a match. `n` changes on every request. */
  pin: { offerId: string; n: number } | null;
  onSelect: (offer: Offer) => void;
  onMatch: (offer: Offer) => void;
}

/**
 * Sellers or Buyers: the filters and the table for one side of the board.
 *
 * Each side is its own instance with its own filters, so the brands a supplier sells
 * never carry over into the buyers' view, and each is kept mounted once opened — going
 * to another tab and back finds it exactly as it was left.
 */
export function OfferBoard({
  side,
  active,
  clients,
  supplierNames,
  refreshKey,
  pin,
  onSelect,
  onMatch,
}: Props) {
  // Opened for the first time by a "show this lot", the board starts pinned rather than
  // loading everything only to replace it a moment later.
  const [filters, setFilters] = useState<OfferFilters>(() =>
    pin ? { ...emptyFilters, offerId: pin.offerId } : emptyFilters,
  );
  const appliedPin = useRef(pin?.n ?? 0);
  const { offers, loading, error, total } = useOffers(side, filters, refreshKey, active);
  const { brands, categories } = useFacets(side);

  // "Show this lot" from a match drawer pins the board to one row.
  useEffect(() => {
    if (pin && pin.n !== appliedPin.current) {
      appliedPin.current = pin.n;
      setFilters({ ...emptyFilters, offerId: pin.offerId });
    }
  }, [pin]);

  // Rows already on screen stay there while a reload runs: blanking the table to say
  // "loading" every time a list lands made the board jump under the trader's cursor.
  const firstLoad = loading && offers.length === 0;

  return (
    <>
      <Filters
        filters={filters}
        brands={brands}
        categories={categories}
        clients={clients}
        clientLabel={side === "sell" ? "sellers" : "buyers"}
        onChange={setFilters}
        total={total}
        showing={offers.length}
      />

      {error && <p className="error">{error}</p>}
      {firstLoad ? (
        <p className="muted">Loading offers…</p>
      ) : filters.offerId && offers.length === 0 && !loading ? (
        // Distinct from "nothing matches those filters", which is what an empty
        // search looks like. Arriving here from a match means one exact row was
        // asked for by id and was not there, and saying so is what makes the
        // difference between a stale board and a broken link visible.
        <p className="empty">
          That lot is not on this board — it may have been sold or withdrawn since the
          match was worked out.{" "}
          <button className="link" onClick={() => setFilters(emptyFilters)}>
            Show the whole board
          </button>
        </p>
      ) : (
        <OfferTable
          offers={offers}
          onSelect={onSelect}
          onMatch={onMatch}
          supplierName={(id) => supplierNames[id] ?? null}
        />
      )}
    </>
  );
}
