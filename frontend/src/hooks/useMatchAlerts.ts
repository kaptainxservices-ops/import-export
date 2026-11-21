import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { MatchBoardRow } from "../lib/api";
import {
  fetchNewMatches,
  latestMatchTime,
  toBoardRow,
  type StoredMatch,
} from "../lib/boardMatches";
import { supabase } from "../lib/supabase";

/** One batch of requirements that turned fillable together. */
export interface MatchAlert {
  id: number;
  rows: MatchBoardRow[];
}

export type DesktopPermission = NotificationPermission | "unsupported";

// One email turns several requirements fillable within a second or two of each other;
// gathering them makes one alert rather than a stutter of them.
const GATHER_MS = 2_500;

/**
 * What this person has seen, kept per browser.
 *
 * `since` is the starting line: matches before it were on the board before the first
 * visit and never count as new. It moves only on a first visit. Past it, a match stops
 * being new when the trader opens it -- one at a time -- not when the Matches screen is
 * opened. `seen` maps each opened requirement to its first_matched_at.
 */
interface SeenState {
  since: string | null;
  seen: Record<string, string>;
}

// Enough for months of matches; the oldest are dropped beyond it.
const MAX_SEEN = 3000;

function storageKey(email: string) {
  return `tvd.matchAlerts.v3:${email}`;
}

// Browser storage can be missing or throw (private windows, blocked site data). The
// cost is only that a reload starts from "now" instead of from the last visit.
function loadState(email: string): SeenState {
  try {
    const raw = window.localStorage.getItem(storageKey(email));
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<SeenState>;
      return { since: parsed.since ?? null, seen: parsed.seen ?? {} };
    }
    // The previous version kept only a moving 'seen up to' time. It is a fair start.
    return { since: window.localStorage.getItem(`tvd.matchAlerts.v2:${email}`), seen: {} };
  } catch {
    return { since: null, seen: {} };
  }
}

function saveState(email: string, state: SeenState) {
  const since = state.since ? Date.parse(state.since) : -Infinity;
  const kept = Object.entries(state.seen)
    // Anything at or before the starting line can never come back as new.
    .filter(([, at]) => Date.parse(at) > since)
    .sort((a, b) => Date.parse(b[1]) - Date.parse(a[1]))
    .slice(0, MAX_SEEN);
  try {
    window.localStorage.setItem(
      storageKey(email),
      JSON.stringify({ since: state.since, seen: Object.fromEntries(kept) }),
    );
  } catch {
    // Alerts still work for this session.
  }
}

function desktopPermission(): DesktopPermission {
  return typeof Notification === "undefined" ? "unsupported" : Notification.permission;
}

/**
 * Tells the trader when a buyer's requirement becomes fillable.
 *
 * The backend stamps `first_matched_at` on a requirement the first time it can be
 * filled, and never again — so a requirement is announced once, a second supplier for
 * an order already announced is not news, and neither is a supplier resending the same
 * list. This listens for those stamps as they are written (realtime on board_matches).
 *
 * The count is a to-do list, not a doorbell: opening the Matches screen does not clear
 * it. Each new match stays counted, and tagged NEW on the board, until the trader opens
 * that match; twenty new matches and one opened leaves nineteen. "Mark all as seen"
 * clears the lot on purpose. A first visit starts from the newest match on the board:
 * nobody opens a new laptop to two hundred alerts.
 *
 * Runs only while the dashboard is open. The stamps are on the server, so email alerts
 * for when it is closed can be built on the same column.
 */
export function useMatchAlerts(
  email: string,
  // A row opens that requirement's options; null opens Matches, newest first.
  onOpen: (row: MatchBoardRow | null) => void,
) {
  const stored = useRef<SeenState>(loadState(email));
  const since = useRef<string | null>(stored.current.since);
  const [unseen, setUnseen] = useState<StoredMatch[]>([]);
  const unseenRef = useRef(unseen);
  unseenRef.current = unseen;

  // NEW tags on the Matches screen: every match not yet opened.
  const highlight = useMemo(() => new Set(unseen.map((r) => r.offer_id)), [unseen]);
  const [alerts, setAlerts] = useState<MatchAlert[]>([]);
  const [permission, setPermission] = useState<DesktopPermission>(desktopPermission);
  // False until the stored board exists (migration 0017); alerts are quiet until then.
  const [available, setAvailable] = useState(true);

  const onOpenRef = useRef(onOpen);
  onOpenRef.current = onOpen;
  // Requirements already shown as a toast this session, so a reload of the unseen
  // list after an event does not toast them again.
  const toasted = useRef(new Set<string>());
  const nextAlert = useRef(1);

  /** Mark these requirements as looked at. */
  const acknowledge = useCallback(
    (ids: string[]) => {
      if (ids.length === 0) return;
      const byId = new Map(unseenRef.current.map((r) => [r.offer_id, r]));
      const now = new Date().toISOString();
      for (const id of ids) {
        stored.current.seen[id] = byId.get(id)?.first_matched_at ?? now;
      }
      saveState(email, stored.current);
      const gone = new Set(ids);
      setUnseen((prev) => prev.filter((r) => !gone.has(r.offer_id)));
    },
    [email],
  );

  const check = useCallback(async () => {
    try {
      if (since.current === null) {
        // A first visit: everything already on the board counts as seen.
        const newest = await latestMatchTime();
        since.current = newest ?? new Date().toISOString();
        stored.current.since = since.current;
        saveState(email, stored.current);
        setAvailable(true);
        return;
      }

      const all = await fetchNewMatches(since.current);
      setAvailable(true);
      // A requirement opened before stays seen -- unless it was matched again later,
      // which the backend never does, so the stamp is compared only to be safe.
      const rows = all.filter((r) => stored.current.seen[r.offer_id] !== r.first_matched_at);
      setUnseen(rows);

      const fresh = rows.filter((r) => !toasted.current.has(r.offer_id));
      if (fresh.length === 0) return;
      for (const row of fresh) toasted.current.add(row.offer_id);

      const shown = fresh.map((r) => toBoardRow(r));
      setAlerts((prev) => [{ id: nextAlert.current++, rows: shown }, ...prev].slice(0, 3));
      notifyDesktop(shown, (row) => onOpenRef.current(row));
    } catch {
      // No stored board yet, or a dropped connection: the next event or focus retries.
      setAvailable(false);
    }
  }, [email]);

  useEffect(() => {
    // Matches already unseen at sign-in show in the badge without a toast: they are
    // old news to a toast, and the count says enough.
    void (async () => {
      if (since.current !== null) {
        try {
          const rows = await fetchNewMatches(since.current);
          for (const row of rows) toasted.current.add(row.offer_id);
        } catch {
          // check() below reports it.
        }
      }
      await check();
    })();

    let gather: number | undefined;
    const channel = supabase
      .channel("match-alerts")
      .on("postgres_changes", { event: "*", schema: "public", table: "board_matches" }, (event) => {
        const row = event.new as Partial<StoredMatch> | undefined;
        // Only a requirement that has just become fillable is worth a look.
        if (!row?.first_matched_at || (row.state !== "filled" && row.state !== "short")) return;
        if (since.current && Date.parse(row.first_matched_at) <= Date.parse(since.current)) return;
        window.clearTimeout(gather);
        gather = window.setTimeout(() => void check(), GATHER_MS);
      })
      .subscribe();

    const onVisible = () => {
      if (document.visibilityState === "visible") void check();
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      window.clearTimeout(gather);
      document.removeEventListener("visibilitychange", onVisible);
      void supabase.removeChannel(channel);
    };
  }, [check]);

  /** One match opened: it stops being new, and the count drops by one. */
  const markSeen = useCallback((offerId: string) => acknowledge([offerId]), [acknowledge]);

  /** Every new match, cleared on purpose. */
  const markAllSeen = useCallback(
    () => acknowledge(unseenRef.current.map((r) => r.offer_id)),
    [acknowledge],
  );

  const dismiss = useCallback((id: number) => {
    setAlerts((prev) => prev.filter((a) => a.id !== id));
  }, []);

  const enableDesktop = useCallback(async () => {
    if (typeof Notification === "undefined") return;
    try {
      setPermission(await Notification.requestPermission());
    } catch {
      setPermission(desktopPermission());
    }
  }, []);

  return {
    unseenCount: unseen.length,
    highlight,
    markSeen,
    markAllSeen,
    alerts,
    dismiss,
    permission,
    enableDesktop,
    available,
  };
}

/** One system notification per batch, however many matched, so a big list is one ping. */
function notifyDesktop(rows: MatchBoardRow[], open: (row: MatchBoardRow | null) => void) {
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return;

  const first = rows[0];
  const title =
    rows.length === 1 ? "New match on the TVD board" : `${rows.length} new matches on the TVD board`;
  const body =
    rows.length === 1
      ? `${first.description} — ${first.counterparty_name ?? "a buyer"} wants ` +
        `${first.quantity?.toLocaleString() ?? "?"}; ` +
        `${first.state === "filled" ? "can fill it all" : `can fill ${first.filled_quantity.toLocaleString()}`}`
      : rows
          .slice(0, 3)
          .map((r) => r.description)
          .join("\n") + (rows.length > 3 ? `\n+${rows.length - 3} more` : "");

  try {
    const notice = new Notification(title, { body, tag: "tvd-match", icon: "/tvd-logo.png" });
    notice.onclick = () => {
      window.focus();
      open(rows.length === 1 ? first : null);
      notice.close();
    };
  } catch {
    // Some browsers only allow notifications from a service worker. The in-app alert
    // has already been shown, so there is nothing else to do.
  }
}
