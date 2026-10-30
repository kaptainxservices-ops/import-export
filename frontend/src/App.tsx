import { useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";

import { Deals } from "./components/Deals";
import { Login } from "./components/Login";
import { MatchBoard } from "./components/MatchBoard";
import { MatchDrawer, type MatchSubject } from "./components/MatchDrawer";
import { MatchToasts } from "./components/MatchToasts";
import { OfferBoard } from "./components/OfferBoard";
import { ReviewQueue } from "./components/ReviewQueue";
import { SourceDrawer } from "./components/SourceDrawer";
import { Suppliers } from "./components/Suppliers";
import { useMatchAlerts } from "./hooks/useMatchAlerts";
import { useClients, useSupplierNames } from "./hooks/useOffers";
import { fetchReviewQueue, type MatchBoardRow, type MatchSort } from "./lib/api";
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
  const [selected, setSelected] = useState<Offer | null>(null);
  const [matching, setMatching] = useState<MatchSubject | null>(null);
  // Bumped when a review is resolved, to pull the board again. Resolving an email is
  // the one action in this app that adds rows, so it is the one that has to invalidate.
  const [resolved, setResolved] = useState(0);

  // Sellers and Buyers are mounted the first time they are opened and kept from then
  // on. Never opened, never loaded: a trader who only works Matches pays nothing for
  // the other two.
  const [opened, setOpened] = useState<Set<Side>>(new Set());
  useEffect(() => {
    if ((tab === "sell" || tab === "buy") && !opened.has(tab)) {
      setOpened((prev) => new Set(prev).add(tab));
    }
  }, [tab, opened]);
  // A lot to show on one side, from a match drawer's "show this lot".
  const [pin, setPin] = useState<{ side: Side; offerId: string; n: number } | null>(null);
  const supplierNames = useSupplierNames();
  const buyers = useClients("buy");
  const sellers = useClients("sell");
  const [matchSort, setMatchSort] = useState<MatchSort>("best");

  function openRequirement(row: MatchBoardRow) {
    setMatching({
      id: row.offer_id,
      side: "buy",
      description: row.description,
      quantity: row.quantity,
      unit_price: row.unit_price,
      currency: row.currency,
    });
  }

  function showNewMatches() {
    setTab("matches");
    setMatchSort("new");
  }

  const matchAlerts = useMatchAlerts(email, tab === "matches", (row) =>
    row ? openRequirement(row) : showNewMatches(),
  );

  // The count sits in the nav so a waiting email is visible from any screen. Fetched
  // once and again after each resolution, which is the only thing that changes it.
  const [waiting, setWaiting] = useState(0);
  useEffect(() => {
    void fetchReviewQueue()
      .then((queue) => setWaiting(queue.total))
      .catch(() => setWaiting(0));
  }, [resolved]);

  // Each side keeps its own filters (see OfferBoard), so switching no longer resets
  // them: the brands a supplier sells never leak into the buyers' view either way.
  function switchTab(next: Tab) {
    setTab(next);
  }

  return (
    <div className="app">
      <header className="top">
        {/* The client's own mark. The trading board is theirs, and it should look it. */}
        <div className="brand">
          <span className="brand-plate">
            <img src="/tvd-logo.png" alt="" width={44} height={36} />
          </span>
          <span className="brand-name">TVD Services</span>
        </div>

        <nav aria-label="Sections">
          <button
            className={tab === "matches" ? "tab active" : "tab"}
            onClick={() => switchTab("matches")}
          >
            Matches
            {matchAlerts.unseenCount > 0 && (
              <span className="nav-count new" title="New matches since you last looked">
                {matchAlerts.unseenCount}
              </span>
            )}
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
          {matchAlerts.permission === "default" && (
            <button
              className="link"
              onClick={() => void matchAlerts.enableDesktop()}
              title="Get a system notification when a buyer's requirement can be filled, even when this tab is in the background"
            >
              Turn on match alerts
            </button>
          )}
          {matchAlerts.permission === "denied" && (
            <span
              className="muted"
              title="This browser is blocking notifications for the site. Allow them in the address bar's site settings to get desktop alerts; in-app alerts still work."
            >
              Desktop alerts blocked
            </span>
          )}
          <span className="muted">{email}</span>
          <button className="link" onClick={() => void supabase.auth.signOut()}>
            Sign out
          </button>
        </div>
      </header>

      <main>
        {/* Kept mounted while another tab is open, only hidden. Unmounting threw away
            the board, the filters and the search, so every return to Matches started
            from nothing and waited for a full load; now it is exactly as it was left,
            and realtime keeps it current in the background. */}
        <div hidden={tab !== "matches"}>
          <MatchBoard
            onOpen={openRequirement}
            buyers={buyers}
            suppliers={sellers}
            sort={matchSort}
            onSortChange={setMatchSort}
            fresh={matchAlerts.highlight}
            refreshKey={resolved}
          />
        </div>

        {(["sell", "buy"] as Side[]).map(
          (side) =>
            opened.has(side) && (
              <div key={side} hidden={tab !== side}>
                <OfferBoard
                  side={side}
                  active={tab === side}
                  clients={side === "sell" ? sellers : buyers}
                  supplierNames={supplierNames}
                  refreshKey={resolved}
                  pin={pin && pin.side === side ? pin : null}
                  onSelect={setSelected}
                  onMatch={setMatching}
                />
              </div>
            ),
        )}

        {tab === "deals" ? (
          <Deals />
        ) : tab === "review" ? (
          <ReviewQueue onResolved={() => setResolved((n) => n + 1)} />
        ) : tab === "suppliers" ? (
          <Suppliers />
        ) : null}
      </main>

      <MatchToasts
        alerts={matchAlerts.alerts}
        onOpen={openRequirement}
        onShowAll={showNewMatches}
        onDismiss={matchAlerts.dismiss}
      />

      {selected && <SourceDrawer offer={selected} onClose={() => setSelected(null)} />}
      {matching && (
        <MatchDrawer
          offer={matching}
          onClose={() => setMatching(null)}
          onShowOffer={(offerId, side) => {
            setMatching(null);
            setTab(side);
            setPin((prev) => ({ side, offerId, n: (prev?.n ?? 0) + 1 }));
          }}
        />
      )}
    </div>
  );
}
