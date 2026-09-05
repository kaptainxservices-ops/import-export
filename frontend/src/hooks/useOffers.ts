import { useCallback, useEffect, useRef, useState } from "react";

import { supabase } from "../lib/supabase";
import type { Offer, Side } from "../lib/types";

const PAGE_SIZE = 500;

export interface OfferFilters {
  search: string;
  brand: string;
  category: string;
  counterparty: string;
  onlyLive: boolean;
  // One specific row, arrived at from a match. Narrower than a search: the trader
  // clicked an exact allocation and wants that lot, not everything resembling it.
  offerId: string;
}

export const emptyFilters: OfferFilters = {
  search: "",
  brand: "",
  category: "",
  counterparty: "",
  onlyLive: true,
  offerId: "",
};

/**
 * Live offers for one side of the board.
 *
 * Filtering happens in Postgres rather than in the browser. A busy morning puts
 * thousands of rows on the board, and shipping all of them to filter client-side is
 * slow on the trader's laptop and wasteful of the free tier's bandwidth.
 *
 * Realtime updates are deliberately coarse: any change to `offers` triggers a refetch
 * rather than a surgical patch of one row. At this volume the refetch is cheap, and a
 * board that quietly diverges from the database is worse than one that reloads.
 */
export function useOffers(side: Side, filters: OfferFilters, refreshKey = 0) {
  const [offers, setOffers] = useState<Offer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [total, setTotal] = useState(0);

  // Keeps the realtime callback from closing over stale filters.
  const filtersRef = useRef(filters);
  filtersRef.current = filters;

  // Only the newest query may write to state. Two loads are easily in flight at once —
  // a filter change starts one, the realtime subscription starts another — and without
  // this the slower, older answer lands last and wins, leaving the board showing rows
  // nobody asked for.
  const sequence = useRef(0);

  const load = useCallback(async () => {
    const current = filtersRef.current;
    const ticket = ++sequence.current;
    setError(null);

    let query = supabase
      .from("offers")
      .select("*", { count: "exact" })
      .eq("side", side)
      .order("last_confirmed_at", { ascending: false })
      .limit(PAGE_SIZE);

    if (current.offerId) {
      // Deliberately before the others: an offer reached from a match should show even
      // if it is closed or outside the current filters, or the click does nothing and
      // looks broken.
      query = query.eq("id", current.offerId);
    } else if (current.onlyLive) {
      query = query.eq("status", "live");
    }
    if (!current.offerId && current.brand) query = query.eq("brand", current.brand);
    if (!current.offerId && current.category) query = query.eq("category", current.category);
    if (!current.offerId && current.counterparty) query = query.eq("counterparty_id", current.counterparty);
    if (!current.offerId && current.search.trim()) {
      const term = `%${current.search.trim()}%`;
      query = query.or(`description.ilike.${term},ean.ilike.${term},model.ilike.${term}`);
    }

    const { data, error: queryError, count } = await query;

    // A newer load started while this one was in flight. Its answer is the true one.
    if (ticket !== sequence.current) return;

    if (queryError) {
      setError(queryError.message);
      // Cleared rather than left standing: rows fetched under the previous filters,
      // displayed under the new ones with an error above them, read as the answer to
      // the question that just failed.
      setOffers([]);
      setTotal(0);
    } else {
      setOffers((data ?? []) as Offer[]);
      setTotal(count ?? 0);
    }
    setLoading(false);
  }, [side]);

  // `refreshKey` covers the case realtime does not: resolving a review inserts rows
  // through the backend, and if realtime is off or the socket has dropped, the board
  // would otherwise sit there looking exactly as empty as before the click.
  useEffect(() => {
    setLoading(true);
    void load();
  }, [load, filters, refreshKey]);

  useEffect(() => {
    const channel = supabase
      .channel(`offers-${side}`)
      .on("postgres_changes", { event: "*", schema: "public", table: "offers" }, () => {
        void load();
      })
      .subscribe();

    return () => {
      void supabase.removeChannel(channel);
    };
  }, [load, side]);

  return { offers, loading, error, total, reload: load };
}

/** Distinct values for the filter dropdowns, taken from what is actually on the board. */
export function useFacets(side: Side) {
  const [brands, setBrands] = useState<string[]>([]);
  const [categories, setCategories] = useState<string[]>([]);

  useEffect(() => {
    void (async () => {
      const { data } = await supabase
        .from("offers")
        .select("brand,category")
        .eq("side", side)
        .eq("status", "live")
        .limit(5000);

      const rows = (data ?? []) as { brand: string | null; category: string | null }[];
      setBrands([...new Set(rows.map((r) => r.brand).filter(Boolean) as string[])].sort());
      setCategories(
        [...new Set(rows.map((r) => r.category).filter(Boolean) as string[])].sort(),
      );
    })();
  }, [side]);

  return { brands, categories };
}

/**
 * Counterparty names, by id.
 *
 * The board stores `counterparty_id` and a trader reads "Al Manar". Fetched once for
 * the whole board rather than joined per row: the same twenty suppliers account for
 * every row on the page, and a join would ship each name hundreds of times.
 */
export function useSupplierNames() {
  const [names, setNames] = useState<Record<string, string>>({});

  useEffect(() => {
    void (async () => {
      const { data } = await supabase
        .from("counterparties")
        .select("id,name,primary_email,country")
        .limit(2000);

      const lookup: Record<string, string> = {};
      for (const row of (data ?? []) as {
        id: string;
        name: string | null;
        primary_email: string;
        country: string | null;
      }[]) {
        const who = row.name || row.primary_email;
        lookup[row.id] = row.country ? `${who} · ${row.country}` : who;
      }
      setNames(lookup);
    })();
  }, []);

  return names;
}
