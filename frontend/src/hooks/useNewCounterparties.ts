import { useCallback, useEffect, useRef, useState } from "react";

import { supabase } from "../lib/supabase";

function storageKey(email: string) {
  return `tvd.counterpartiesSeen.v1:${email}`;
}

function load(email: string): string | null {
  try {
    return window.localStorage.getItem(storageKey(email));
  } catch {
    return null;
  }
}

function save(email: string, since: string) {
  try {
    window.localStorage.setItem(storageKey(email), since);
  } catch {
    // The badge still works for this session.
  }
}

/**
 * Counterparties that appeared since this person last opened the Counterparties tab.
 *
 * Every new sender becomes a counterparty automatically -- including suppliers opened
 * from a retyped offer's subject line, with no address yet -- and each one wants a
 * look: their currency and number format decide how their prices are read. The count
 * sits on the tab so a new one is noticed from any screen.
 *
 * "Last looked" is the newest `created_at` seen, a server timestamp, so this laptop's
 * clock cannot hide one or count one twice. A first visit starts from now: an existing
 * list of sixty suppliers is not sixty pieces of news.
 */
export function useNewCounterparties(email: string, watching: boolean) {
  const since = useRef<string | null>(load(email));
  const [fresh, setFresh] = useState<{ id: string; created_at: string }[]>([]);
  const freshRef = useRef(fresh);
  freshRef.current = fresh;
  // Highlighted on the tab while it is open: what was new when it was opened.
  const [highlight, setHighlight] = useState<Set<string>>(new Set());
  const watchingRef = useRef(watching);
  watchingRef.current = watching;

  const acknowledge = useCallback(
    (rows: { created_at: string }[]) => {
      let mark = since.current;
      for (const row of rows) {
        if (!mark || Date.parse(row.created_at) > Date.parse(mark)) mark = row.created_at;
      }
      if (mark && mark !== since.current) {
        since.current = mark;
        save(email, mark);
      }
    },
    [email],
  );

  const check = useCallback(async () => {
    if (since.current === null) {
      const { data } = await supabase
        .from("counterparties")
        .select("created_at")
        .order("created_at", { ascending: false })
        .limit(1);
      since.current = (data?.[0]?.created_at as string | undefined) ?? new Date().toISOString();
      save(email, since.current);
      return;
    }
    const { data, error } = await supabase
      .from("counterparties")
      .select("id,created_at")
      .gt("created_at", since.current)
      .order("created_at", { ascending: false })
      .limit(200);
    if (error) return;
    const rows = (data ?? []) as { id: string; created_at: string }[];
    if (watchingRef.current) {
      if (rows.length) setHighlight((prev) => new Set([...prev, ...rows.map((r) => r.id)]));
      acknowledge(rows);
      setFresh([]);
    } else {
      setFresh(rows);
    }
  }, [acknowledge, email]);

  useEffect(() => {
    void check();
    const channel = supabase
      .channel("new-counterparties")
      .on("postgres_changes", { event: "INSERT", schema: "public", table: "counterparties" }, () =>
        void check(),
      )
      .subscribe();
    // A slow fallback for a dropped socket or a table not yet published for realtime.
    const heartbeat = window.setInterval(() => void check(), 5 * 60_000);
    const onVisible = () => {
      if (document.visibilityState === "visible") void check();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(heartbeat);
      document.removeEventListener("visibilitychange", onVisible);
      void supabase.removeChannel(channel);
    };
  }, [check]);

  useEffect(() => {
    if (watching) {
      const rows = freshRef.current;
      if (rows.length) {
        setHighlight((prev) => new Set([...prev, ...rows.map((r) => r.id)]));
        acknowledge(rows);
        setFresh([]);
      }
    } else {
      setHighlight(new Set());
    }
  }, [watching, acknowledge]);

  return { newCount: fresh.length, highlight };
}
