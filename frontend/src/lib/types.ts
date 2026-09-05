export type Side = "sell" | "buy";
export type OfferStatus = "live" | "sold" | "withdrawn" | "expired";

export interface Offer {
  id: string;
  counterparty_id: string;
  side: Side;
  status: OfferStatus;

  brand: string | null;
  category: string | null;
  ean: string | null;
  description: string | null;
  model: string | null;
  capacity_gb: number | null;
  colour: string | null;
  region_code: string | null;
  grade: string | null;
  incoterm: string | null;

  quantity: number | null;
  unit_price: number | null;
  currency: string | null;

  first_seen_at: string;
  last_confirmed_at: string;
  source_email_id: string | null;
  source_ref: string | null;
  in_conversation: boolean;
}

export interface Counterparty {
  id: string;
  name: string | null;
  primary_email: string;
  country: string | null;
}

export interface SourceEmail {
  id: string;
  subject: string | null;
  from_email: string;
  received_at: string;
  body_text: string | null;
  body_raw: string | null;
  needs_sender_review: boolean;
}

/**
 * How stale a row is.
 *
 * Suppliers send a complete list every day, so an offer that has not been confirmed
 * today is either from an irregular sender or something went wrong with the import.
 * Either way the trader should see it before quoting the price to anyone.
 */
export type Freshness = "today" | "recent" | "stale";

export function freshness(lastConfirmed: string, hours = 24): Freshness {
  const age = (Date.now() - new Date(lastConfirmed).getTime()) / 3_600_000;
  if (age <= hours) return "today";
  if (age <= hours * 3) return "recent";
  return "stale";
}

/**
 * "2h ago", "9h ago", "6 days".
 *
 * Absolute timestamps make the reader do arithmetic. The only question being asked of
 * this column is "is this still true?", and that is a question about elapsed time.
 */
export function relativeAge(lastConfirmed: string): string {
  const minutes = (Date.now() - new Date(lastConfirmed).getTime()) / 60_000;

  if (minutes < 2) return "just now";
  if (minutes < 60) return `${Math.round(minutes)}m ago`;

  const hours = minutes / 60;
  if (hours < 24) return `${Math.round(hours)}h ago`;

  const days = Math.round(hours / 24);
  return days === 1 ? "yesterday" : `${days} days`;
}
