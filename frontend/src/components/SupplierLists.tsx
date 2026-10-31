import { useEffect, useState } from "react";

import { fetchSupplierLists, type SupplierList } from "../lib/api";

const LABEL: Record<SupplierList["action"], string> = {
  replace: "Replaced",
  add: "Added",
  add_new_only: "New lines only",
  refresh: "Resent, unchanged",
  reject: "Ignored (older)",
  held: "Held",
  review: "Sent to Review",
  unreadable: "Unreadable",
};

// What each decision means, in a trader's words, for the tooltip on its badge.
const MEANING: Record<SupplierList["action"], string> = {
  replace: "Their whole list: new lines added, changes made, missing lines closed as sold.",
  add: "Extra stock or an order: new lines added and changes made, nothing closed.",
  add_new_only:
    "Arrived after a newer list from them: only lines the board never had were added.",
  refresh: "Identical to a list already on the board today: only its timestamp moved.",
  reject: "A list from them on a later day already covered this: nothing applied.",
  held: "Waiting for a person to say what it is.",
  review: "Waiting in Review.",
  unreadable: "Nothing could be read from it.",
};

function when(stamp: string | null): string {
  if (!stamp) return "";
  return new Date(stamp).toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Every recent list from one counterparty, and what the engine did with it.
 *
 * This is where the replace/add rules are checked against reality: if a supplier's
 * stock disappeared, the list that closed it is here with the reason it was read as
 * their whole list.
 */
export function SupplierLists({ id }: { id: string }) {
  const [items, setItems] = useState<SupplierList[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetchSupplierLists(id)
      .then((lists) => live && setItems(lists))
      .catch((problem) => live && setError(problem instanceof Error ? problem.message : String(problem)));
    return () => {
      live = false;
    };
  }, [id]);

  if (error) return <p className="error small">{error}</p>;
  if (items === null) return <p className="muted small">Loading lists…</p>;
  if (items.length === 0) return <p className="muted small">No lists from them yet.</p>;

  return (
    <ol className="list-history">
      {items.map((item) => (
        <li key={item.id}>
          <div className="list-head">
            <span className={`decision ${item.action}`} title={MEANING[item.action]}>
              {LABEL[item.action]}
            </span>
            {item.checked_by_model && (
              <span className="tag" title={`Decided by: ${item.decided_by}`}>
                checked by AI
              </span>
            )}
            <span className="muted small">
              {when(item.received_at)}
              {item.side && ` · ${item.side === "sell" ? "selling" : "buying"}`}
            </span>
            <span className="list-subject">{item.subject || "(no subject)"}</span>
            <span className="list-counts small">
              {item.rows} lines
              {item.added > 0 && <> · <b className="up">+{item.added}</b> new</>}
              {item.updated > 0 && <> · {item.updated} changed</>}
              {item.closed > 0 && <> · <b className="down">{item.closed}</b> closed</>}
            </span>
          </div>
          {item.reasons.length > 0 && (
            <div className="muted small list-why">{item.reasons.join(" · ")}</div>
          )}
        </li>
      ))}
    </ol>
  );
}
