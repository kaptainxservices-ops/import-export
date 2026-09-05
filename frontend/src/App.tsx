import { useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";

import { Deals } from "./components/Deals";
import { Filters } from "./components/Filters";
import { Login } from "./components/Login";
import { MatchBoard } from "./components/MatchBoard";
import { MatchDrawer, type MatchSubject } from "./components/MatchDrawer";
import { OfferTable } from "./components/OfferTable";
import { ReviewQueue } from "./components/ReviewQueue";
import { SourceDrawer } from "./components/SourceDrawer";
import { Suppliers } from "./components/Suppliers";
import {
  emptyFilters,
  useFacets,
  useOffers,
  useSupplierNames,
  type OfferFilters,
} from "./hooks/useOffers";
import { fetchReviewQueue } from "./lib/api";
import { supabase } from "./lib/supabase";
import type { Offer, Side } from "./lib/types";

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    void supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setChecking(false);
    });

    const { data: listener } = supabase.auth.onAuthStateChange((_event, next) => {
      setSession(next);
    });

    return () => listener.subscription.unsubscribe();
  }, []);

  if (checking) return <div className="centre muted">Loading…</div>;
  if (!session) return <Login />;

  return <Board email={session.user.email ?? ""} />;
}

type Tab = Side | "matches" | "deals" | "review" | "suppliers";

function Board({ email }: { email: string }) {
  const [tab, setTab] = useState<Tab>("matches");
  const [filters, setFilters] = useState<OfferFilters>(emptyFilters);
  const [selected, setSelected] = useState<Offer | null>(null);
  const [matching, setMatching] = useState<MatchSubject | null>(null);
  // Bumped when a review is resolved, to pull the board again. Resolving an email is
  // the one action in this app that adds rows, so it is the one that has to invalidate.
  const [resolved, setResolved] = useState(0);

  const side: Side = tab === "sell" || tab === "buy" ? tab : "sell";
  const { offers, loading, error, total } = useOffers(side, filters, resolved);
  const { brands, categories } = useFacets(side);
  const supplierNames = useSupplierNames();

  // The count sits in the nav so a waiting email is visible from any screen. Fetched
  // once and again after each resolution, which is the only thing that changes it.
  const [waiting, setWaiting] = useState(0);
  useEffect(() => {
    void fetchReviewQueue()
      .then((queue) => setWaiting(queue.total))
      .catch(() => setWaiting(0));
  }, [resolved]);

  // Filters are per-side: the brands a supplier sells are not the brands a buyer wants,
  // and carrying a stale brand filter across the switch shows an empty board.
  function switchTab(next: Tab) {
    setTab(next);
    if (next === "sell" || next === "buy") setFilters(emptyFilters);
  }

  return (
    <div className="app">
      <header className="top">
        <nav>
          <button
            className={tab === "matches" ? "tab active" : "tab"}
            onClick={() => switchTab("matches")}
          >
            Matches
          </button>
          <button
            className={tab === "sell" ? "tab active" : "tab"}
            onClick={() => switchTab("sell")}
          >
            Sellers
          </button>
          <button
            className={tab === "buy" ? "tab active" : "tab"}
            onClick={() => switchTab("buy")}
          >
            Buyers
          </button>
          <button
            className={tab === "deals" ? "tab active" : "tab"}
            onClick={() => switchTab("deals")}
          >
            Deals
          </button>
          <button
            className={tab === "review" ? "tab active" : "tab"}
            onClick={() => switchTab("review")}
          >
            Review
            {waiting > 0 && <span className="nav-count">{waiting}</span>}
          </button>
          <button
            className={tab === "suppliers" ? "tab active" : "tab"}
            onClick={() => switchTab("suppliers")}
          >
            Counterparties
          </button>
        </nav>

        <div className="account">
          <span className="muted">{email}</span>
          <button className="link" onClick={() => void supabase.auth.signOut()}>
            sign out
          </button>
        </div>
      </header>

      <main>
        {tab === "matches" ? (
          <MatchBoard
            onOpen={(row) =>
              setMatching({
                id: row.offer_id,
                side: "buy",
                description: row.description,
                quantity: row.quantity,
                unit_price: row.unit_price,
                currency: row.currency,
              })
            }
          />
        ) : tab === "deals" ? (
          <Deals />
        ) : tab === "review" ? (
          <ReviewQueue onResolved={() => setResolved((n) => n + 1)} />
        ) : tab === "suppliers" ? (
          <Suppliers />
        ) : (
          <>
            <Filters
              filters={filters}
              brands={brands}
              categories={categories}
              onChange={setFilters}
              total={total}
              showing={offers.length}
            />

            {error && <p className="error">{error}</p>}
            {loading ? (
              <p className="muted">Loading offers…</p>
            ) : filters.offerId && offers.length === 0 ? (
              // Distinct from "nothing matches those filters", which is what an empty
              // search looks like. Arriving here from a match means one exact row was
              // asked for by id and was not there, and saying so is what makes the
              // difference between a stale board and a broken link visible.
              <p className="empty">
                That lot is not on this board — it may have been sold or withdrawn since
                the match was worked out.{" "}
                <button className="link" onClick={() => setFilters(emptyFilters)}>
                  Show the whole board
                </button>
              </p>
            ) : (
              <OfferTable
                offers={offers}
                onSelect={setSelected}
                onMatch={setMatching}
                supplierName={(id) => supplierNames[id] ?? null}
              />
            )}
          </>
        )}
      </main>

      {selected && <SourceDrawer offer={selected} onClose={() => setSelected(null)} />}
      {matching && (
        <MatchDrawer
          offer={matching}
          onClose={() => setMatching(null)}
          onShowOffer={(offerId, side) => {
            setMatching(null);
            setTab(side);
            setFilters({ ...emptyFilters, offerId });
          }}
        />
      )}
    </div>
  );
}
