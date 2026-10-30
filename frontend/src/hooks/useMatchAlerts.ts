import { useCallback, useEffect, useRef, useState } from "react";

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

function storageKey(email: string) {
  return `tvd.matchAlerts.v2:${email}`;
}

// Browser storage can be missing or throw (private windows, blocked site data). The
// cost is only that a reload starts from "now" instead of from the last visit.
function loadSince(email: string): string | null {
  try {
    return window.localStorage.getItem(storageKey(email));
  } catch {
    return null;
  }
}

function saveSince(email: string, since: string) {
  try {
    window.localStorage.setItem(storageKey(email), since);
  } catch {
    // Alerts still work for this session.
  }
}

function desktopPermission(): DesktopPermission {
  return typeof Notification === "undefined" ? "unsupported" : Notification.permission;
}

const later = (a: string, b: string) => (Date.parse(a) >= Date.parse(b) ? a : b);

/**
 * Tells the trader when a buyer's requirement becomes fillable.
 *
 * The backend stamps `first_matched_at` on a requirement the first time it can be
 * filled, and never again — so a requirement is announced once, a second supplier for
 * an order already announced is not news, and neither is a supplier resending the same
 * list. This listens for those stamps as they are written (realtime on board_matches)
 * and compares them with the newest one this person has already looked at.
 *
 * "Already looked at" is a server timestamp, not this laptop's clock, so a fast or
 * slow clock cannot hide a match or repeat one. A first visit starts from the newest
 * match on the board: nobody opens a new laptop to two hundred alerts.
 *
 * Runs only while the dashboard is open. The stamps are on the server, so email alerts
 * for when it is closed can be built on the same column.
 */
export function useMatchAlerts(
  email: string,
  watching: boolean,
  // A row opens that requirement's options; null opens Matches, newest first.
  onOpen: (row: MatchBoardRow | null) => void,
) {
  const since = useRef<string | null>(loadSince(email));
  const [unseen, setUnseen] = useState<StoredMatch[]>([]);
  const unseenRef = useRef(unseen);
  unseenRef.current = unseen;

  // NEW tags on the Matches screen: what was unseen when the trader opened it, plus
  // anything that arrives while they are looking at it.
  const [highlight, setHighlight] = useState<Set<string>>(new Set());
  const [alerts, setAlerts] = useState<MatchAlert[]>([]);
  const [permission, setPermission] = useState<DesktopPermission>(desktopPermission);
  // False until the stored board exists (migration 0017); alerts are quiet until then.
  const [available, setAvailable] = useState(true);

  const watchingRef = useRef(watching);
  watchingRef.current = watching;
  const onOpenRef = useRef(onOpen);
  onOpenRef.current = onOpen;
  // Requirements already shown as a toast this session, so a reload of the unseen
  // list after an event does not toast them again.
  const toasted = useRef(new Set<string>());
  const nextAlert = useRef(1);

  const acknowledge = useCallback(
    (rows: StoredMatch[]) => {
      let mark = since.current;
      for (const row of rows) {
        if (row.first_matched_at) mark = mark ? later(mark, row.first_matched_at) : row.first_matched_at;
      }
      if (mark && mark !== since.current) {
        since.current = mark;
        saveSince(email, mark);
      }
    },
    [email],
  );

  const check = useCallback(async () => {
    try {
      if (since.current === null) {
        // A first visit: everything already on the board counts as seen.
        const newest = await latestMatchTime();
        since.current = newest ?? new Date().toISOString();
        saveSince(email, since.current);
        setAvailable(true);
        return;
      }

      const rows = await fetchNewMatches(since.current);
      setAvailable(true);

      if (watchingRef.current) {
        // On the Matches screen: tagged NEW where they sit, and seen.
        if (rows.length) setHighlight((prev) => new Set([...prev, ...rows.map((r) => r.offer_id)]));
        acknowledge(rows);
        setUnseen([]);
      } else {
        setUnseen(rows);
      }

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
  }, [acknowledge, email]);

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

  // Opening Matches is looking at them: the badge clears and the rows carry NEW until
  // the trader moves to another screen.
  useEffect(() => {
    if (watching) {
      const rows = unseenRef.current;
      if (rows.length > 0) {
        setHighlight((prev) => new Set([...prev, ...rows.map((r) => r.offer_id)]));
        acknowledge(rows);
        setUnseen([]);
      }
    } else {
      setHighlight(new Set());
    }
  }, [watching, acknowledge]);

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
