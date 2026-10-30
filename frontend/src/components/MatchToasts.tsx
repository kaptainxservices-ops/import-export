import { useEffect } from "react";

import type { MatchAlert } from "../hooks/useMatchAlerts";
import type { MatchBoardRow } from "../lib/api";

const STAY_MS = 20_000;

interface Props {
  alerts: MatchAlert[];
  onOpen: (row: MatchBoardRow) => void;
  onShowAll: () => void;
  onDismiss: (id: number) => void;
}

/**
 * New matches, in the corner of whatever screen the trader is on.
 *
 * Polite rather than assertive to a screen reader, and gone by themselves after a
 * while: the count on the Matches tab and the NEW tags keep the record, so a missed
 * toast loses nothing.
 */
export function MatchToasts({ alerts, onOpen, onShowAll, onDismiss }: Props) {
  if (alerts.length === 0) return null;

  return (
    <div className="toasts" role="status" aria-live="polite">
      {alerts.map((alert) => (
        <Toast
          key={alert.id}
          alert={alert}
          onOpen={onOpen}
          onShowAll={onShowAll}
          onDismiss={onDismiss}
        />
      ))}
    </div>
  );
}

function Toast({
  alert,
  onOpen,
  onShowAll,
  onDismiss,
}: {
  alert: MatchAlert;
  onOpen: (row: MatchBoardRow) => void;
  onShowAll: () => void;
  onDismiss: (id: number) => void;
}) {
  useEffect(() => {
    const timer = window.setTimeout(() => onDismiss(alert.id), STAY_MS);
    return () => window.clearTimeout(timer);
  }, [alert.id, onDismiss]);

  const rows = alert.rows;
  const single = rows.length === 1 ? rows[0] : null;

  return (
    <div className="toast">
      <div className="toast-head">
        <span className="toast-title">
          {single ? "New match" : `${rows.length} new matches`}
        </span>
        <button
          className="toast-close"
          aria-label="Dismiss"
          onClick={() => onDismiss(alert.id)}
        >
          ×
        </button>
      </div>

      <ul className="toast-rows">
        {rows.slice(0, 3).map((row) => (
          <li key={row.offer_id}>
            <button
              className="toast-row"
              onClick={() => {
                onOpen(row);
                onDismiss(alert.id);
              }}
            >
              <span className="toast-item">{row.description}</span>
              <span className="toast-sub">
                {row.counterparty_name ?? "Buyer not named"} wants{" "}
                {row.quantity?.toLocaleString() ?? "?"} ·{" "}
                {row.state === "filled"
                  ? "can fill it all"
                  : `can fill ${row.filled_quantity.toLocaleString()}`}
              </span>
            </button>
          </li>
        ))}
      </ul>

      {rows.length > 1 && (
        <button
          className="link toast-all"
          onClick={() => {
            onShowAll();
            onDismiss(alert.id);
          }}
        >
          {rows.length > 3 ? `See all ${rows.length} on Matches` : "See them on Matches"}
        </button>
      )}
    </div>
  );
}
