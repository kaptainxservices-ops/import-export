import { useCallback, useEffect, useRef, useState } from "react";

import { fetchMatchBoard, type MatchBoardRow } from "../lib/api";
import { supabase } from "../lib/supabase";

/** One batch of requirements that turned fillable at the same check. */
export interface MatchAlert {
  id: number;
  rows: MatchBoardRow[];
}

export type DesktopPermission = NotificationPermission | "unsupported";

// A slow heartbeat. Realtime is the main trigger; this covers a dropped socket.
const CHECK_EVERY_MS = 3 * 60_000;
// An ingest writes a whole price list row by row. Waiting for it to settle turns
// three hundred change events into one check, and one alert instead of a stutter.
const SETTLE_MS = 8_000;
// The watcher asks for the board's maximum, so a requirement does not look new merely
// because it sorted into the window this time and out of it last time.
const BOARD_LIMIT = 1000;

interface Stored {
  /** Fillable requirements this browser has already announced. */
  seen: string[];
  /** Announced but not yet looked at: the count on the Matches tab. */
  unseen: string[];
}

function storageKey(email: string) {
  return `tvd.matchAlerts.v1:${email}`;
}

// Browser storage can be missing or throw (private windows, blocked site data). The
// cost is only that the first check after a reload re-seeds silently instead of
// alerting, so every access is wrapped and a failure is treated as "nothing stored".
function load(email: string): Stored | null {
  try {
    const raw = window.localStorage.getItem(storageKey(email));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<Stored>;
    return { seen: parsed.seen ?? [], unseen: parsed.unseen ?? [] };
  } catch {
    return null;
  }
}

function save(email: string, value: Stored) {
  try {
    window.localStorage.setItem(storageKey(email), JSON.stringify(value));
  } catch {
    // Alerts still work for this session; they just are not remembered.
  }
}

function desktopPermission(): DesktopPermission {
  return typeof Notification === "undefined" ? "unsupported" : Notification.permission;
}

const fillable = (row: MatchBoardRow) => row.state === "filled" || row.state === "short";

/**
 * Tells the trader when a buyer's requirement becomes fillable.
 *
 * "A match" here is a requirement going from nothing-can-fill-it to at least one real
 * option, the moment money becomes possible. It is announced once per requirement: a
 * second supplier for an order already announced, or a supplier resending the same
 * list, is not news. Near misses are never announced; they are not a fill.
 *
 * Per person and per browser, deliberately. The first check after signing in learns
 * what is already on the board without announcing it, so a new laptop does not open
 * on two hundred alerts for matches everyone has known about for a week.
 *
 * Runs only while the dashboard is open. With every tab closed nobody is told, which
 * is the limit of a browser alert — an email or WhatsApp alert would need the server
 * to do this instead.
 */
export function useMatchAlerts(
  email: string,
  watching: boolean,
  // A row opens that requirement's options; null opens Matches, newest first.
  onOpen: (row: MatchBoardRow | null) => void,
) {
  const initial = useRef(load(email));
  // null until the first check has learned what is already on the board.
  const seen = useRef<Set<string> | null>(
    initial.current ? new Set(initial.current.seen) : null,
  );
  const [unseen, setUnseen] = useState<string[]>(initial.current?.unseen ?? []);
  const unseenRef = useRef(unseen);
  unseenRef.current = unseen;

  // NEW tags on the Matches screen: what was unseen when the trader opened it, plus
  // anything that arrives while they are looking at it.
  const [highlight, setHighlight] = useState<Set<string>>(new Set());
  const [alerts, setAlerts] = useState<MatchAlert[]>([]);
  // Bumped whenever new matches are found, so an open Matches screen reloads.
  const [version, setVersion] = useState(0);
  const [permission, setPermission] = useState<DesktopPermission>(desktopPermission);

  const watchingRef = useRef(watching);
  watchingRef.current = watching;
  const onOpenRef = useRef(onOpen);
  onOpenRef.current = onOpen;
  const busy = useRef(false);
  const nextAlert = useRef(1);

  const persist = useCallback(
    (nextUnseen: string[]) => {
      if (seen.current) save(email, { seen: [...seen.current], unseen: nextUnseen });
    },
    [email],
  );

  const check = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    try {
      const board = await fetchMatchBoard({ limit: BOARD_LIMIT });
      const matched = board.rows.filter(fillable);

      if (seen.current === null) {
        seen.current = new Set(matched.map((r) => r.offer_id));
        persist(unseenRef.current);
        return;
      }

      // Forget requirements that have left the board (sold, withdrawn), so the store
      // does not grow forever. Only when the whole board came back — a capped answer
      // would make a requirement look gone when it merely sorted past the cap.
      if (board.rows.length < BOARD_LIMIT) {
        const present = new Set(board.rows.map((r) => r.offer_id));
        for (const id of [...seen.current]) if (!present.has(id)) seen.current.delete(id);
      }

      const fresh = matched
        .filter((r) => !seen.current!.has(r.offer_id))
        .sort((a, b) => (b.matched_at ?? "").localeCompare(a.matched_at ?? ""));
      if (fresh.length === 0) {
        persist(unseenRef.current);
        return;
      }

      const ids = fresh.map((r) => r.offer_id);
      for (const id of ids) seen.current.add(id);

      let nextUnseen = unseenRef.current;
      if (watchingRef.current) {
        setHighlight((prev) => new Set([...prev, ...ids]));
      } else {
        nextUnseen = [...new Set([...ids, ...unseenRef.current])];
        setUnseen(nextUnseen);
      }
      persist(nextUnseen);

      setAlerts((prev) => [{ id: nextAlert.current++, rows: fresh }, ...prev].slice(0, 3));
      setVersion((v) => v + 1);
      notifyDesktop(fresh, (row) => onOpenRef.current(row));
    } catch {
      // A failed check is retried by the next trigger. Alerting on a guess is worse.
    } finally {
      busy.current = false;
    }
  }, [persist]);

  // Triggers: now, on a slow heartbeat, when the offers table changes, and when the
  // trader comes back to a tab that has been in the background.
  useEffect(() => {
    void check();
    const heartbeat = window.setInterval(() => void check(), CHECK_EVERY_MS);

    let settle: number | undefined;
    const channel = supabase
      .channel("match-alerts")
      .on("postgres_changes", { event: "*", schema: "public", table: "offers" }, () => {
        window.clearTimeout(settle);
        settle = window.setTimeout(() => void check(), SETTLE_MS);
      })
      .subscribe();

    const onVisible = () => {
      if (document.visibilityState === "visible") void check();
    };
    document.addEventListener("visibilitychange", onVisible);

    return () => {
      window.clearInterval(heartbeat);
      window.clearTimeout(settle);
      document.removeEventListener("visibilitychange", onVisible);
      void supabase.removeChannel(channel);
    };
  }, [check]);

  // Opening Matches is looking at them: the badge clears and the rows carry NEW
  // until the trader moves to another screen.
  useEffect(() => {
    if (watching) {
      if (unseenRef.current.length > 0) {
        setHighlight((prev) => new Set([...prev, ...unseenRef.current]));
        setUnseen([]);
        persist([]);
      }
    } else {
      setHighlight(new Set());
    }
  }, [watching, persist]);

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
    version,
    permission,
    enableDesktop,
  };
}

/** One system notification per check, however many matched, so a big list is one ping. */
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
