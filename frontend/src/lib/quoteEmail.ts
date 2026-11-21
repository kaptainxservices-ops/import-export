import type { Matches, MatchOption } from "./api";

/**
 * One quote email for one buyer, built from the deals the client ticked.
 *
 * Every figure here comes from the database or from the client's own margin. The text
 * around them is a template he can edit freely; the optional AI polish on the server
 * may change that text and is refused if it changes a single figure.
 */

export interface QuoteLine {
  requirementId: string;
  option: MatchOption;
  description: string;
  quantity: number;
  requested: number | null;
  /** Per unit, what the lot costs. Null for a lot listed without a price. */
  cost: number | null;
  currency: string | null;
  marginPct: number;
  /** Per unit, what the buyer is quoted: cost plus margin, to the cent. */
  unitPrice: number | null;
  total: number | null;
  notes: string[];
  suppliers: string[];
}

const round2 = (value: number) => Math.round(value * 100) / 100;

/**
 * Figures in the email are written the international way, '15,450.00', whatever the
 * browser's own locale. A buyer in Dubai reading '1,54,500.00' from an Indian laptop
 * would read a different number.
 */
export function amount(value: number, currency: string | null): string {
  const text = value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const code = (currency ?? "").toUpperCase();
  const symbol: Record<string, string> = { USD: "$", EUR: "€", GBP: "£" };
  if (!code) return text;
  return symbol[code] ? `${symbol[code]}${text}` : `${code} ${text}`;
}

export function buildLine(
  requirement: Matches,
  option: MatchOption,
  marginPct: number,
): QuoteLine {
  const legs = option.allocations;
  const descriptions = new Set(legs.map((a) => a.description));
  const description =
    descriptions.size === 1 ? legs[0].description : requirement.subject_description;
  const cost = option.blended_unit_cost;
  const unitPrice = cost === null ? null : round2(cost * (1 + marginPct / 100));
  const quantity = option.filled_quantity;

  const notes: string[] = [];
  if (option.kind === "near_miss") {
    for (const d of option.differences ?? []) {
      notes.push(`${d.label}: ${d.offered} (you asked for ${d.wanted})`);
    }
    if (!option.differences?.length && option.relaxed.length) notes.push(...option.relaxed);
  }
  if (option.shortfall > 0 && option.requested_quantity) {
    notes.push(`${quantity} of the ${option.requested_quantity} pcs you asked for`);
  }
  if (cost === null) notes.push("Price on request");

  return {
    requirementId: requirement.subject_id,
    option,
    description,
    quantity,
    requested: option.requested_quantity,
    cost,
    currency: legs[0]?.currency ?? requirement.subject_currency,
    marginPct,
    unitPrice,
    total: unitPrice === null ? null : round2(unitPrice * quantity),
    notes,
    suppliers: [
      ...new Set(
        legs.map((a) =>
          [a.counterparty_name ?? "Unnamed supplier", a.country].filter(Boolean).join(", "),
        ),
      ),
    ],
  };
}

/** Totals by currency: lines in two currencies are never added together. */
export function totals(lines: QuoteLine[]): { currency: string | null; total: number }[] {
  const by = new Map<string | null, number>();
  for (const line of lines) {
    if (line.total === null) continue;
    const key = (line.currency ?? "").toUpperCase() || null;
    by.set(key, round2((by.get(key) ?? 0) + line.total));
  }
  return [...by.entries()].map(([currency, total]) => ({ currency, total }));
}

export function buildEmail(
  buyerName: string | null,
  lines: QuoteLine[],
  showSuppliers: boolean,
): { subject: string; body: string } {
  const first = lines[0]?.description ?? "stock";
  const subject =
    lines.length > 1 ? `Offer: ${first} + ${lines.length - 1} more` : `Offer: ${first}`;

  const greeting = personName(buyerName) ? `Hi ${personName(buyerName)},` : "Hi,";
  const body: string[] = [
    greeting,
    "",
    lines.length > 1
      ? "Further to your enquiry, we can offer the following:"
      : "Further to your enquiry, we can offer:",
    "",
  ];

  lines.forEach((line, i) => {
    body.push(`${i + 1}. ${line.description}`);
    const price =
      line.unitPrice === null
        ? "Price: on request"
        : `Price: ${amount(line.unitPrice, line.currency)} per unit`;
    const total = line.total === null ? "" : ` | Total: ${amount(line.total, line.currency)}`;
    body.push(`   Qty: ${line.quantity} pcs | ${price}${total}`);
    for (const note of line.notes.filter((n) => n !== "Price on request")) {
      body.push(`   Note: ${note}`);
    }
    if (showSuppliers) body.push(`   Supplier: ${line.suppliers.join(" + ")}`);
    body.push("");
  });

  const sums = totals(lines);
  if (lines.length > 1 && sums.length > 0) {
    body.push(
      `Total: ${sums.map((s) => amount(s.total, s.currency)).join(" + ")}`,
      "",
    );
  }

  body.push(
    "All offers are subject to stock availability at the time of confirmation.",
    "",
    "Please let us know if you would like to proceed.",
    "",
    "Best regards,",
    "TVD Services",
  );
  return { subject, body: body.join("\n") };
}

// Counterparty names are often an address, a domain or a company: 'Phoneport24.de',
// 'sales@x.com', 'GOtel GmbH', 'Team'. Greeting those by name reads like a mail merge,
// so the first word is used only when the name looks like a person's.
const NOT_A_PERSON = /^(team|sales|info|office|admin|purchase|purchasing|buying|orders|trading|export|support|contact|executive|the)$/i;
const COMPANY = /\b(gmbh|ltd|llc|inc|sa|srl|bv|ab|as|kg|co|corp|limited|trading|electronics|telecom|group)\b/i;

export function personName(name: string | null): string | null {
  const text = (name ?? "").trim();
  if (!text || /[@.|\d]/.test(text) || COMPANY.test(text)) return null;
  const first = text.split(/\s+/)[0];
  return NOT_A_PERSON.test(first) ? null : first;
}

// ---------------------------------------------------------------- to a seller

/**
 * One line of an enquiry to a seller: how many of their lot the client wants and at
 * the seller's own price. No margin here -- the seller's price is the seller's number.
 */
export function buildSellerLine(lot: Matches, option: MatchOption): QuoteLine {
  const quantity = option.filled_quantity;
  const price = lot.subject_price;
  const notes: string[] = [];
  if (lot.subject_quantity && quantity < lot.subject_quantity) {
    notes.push(`${quantity} of the ${lot.subject_quantity} pcs you offered`);
  }
  if (price === null) notes.push("Price on request");
  return {
    requirementId: lot.subject_id,
    option,
    description: lot.subject_description,
    quantity,
    requested: lot.subject_quantity,
    cost: price,
    currency: lot.subject_currency,
    marginPct: 0,
    unitPrice: price === null ? null : round2(price),
    total: price === null ? null : round2(price * quantity),
    notes,
    // Who the lot would go to. Hidden from the seller unless the client shows it.
    suppliers: [
      ...new Set(
        option.allocations.map((a) =>
          [a.counterparty_name ?? "Unnamed buyer", a.country].filter(Boolean).join(", "),
        ),
      ),
    ],
  };
}

export function buildSellerEmail(
  sellerName: string | null,
  lines: QuoteLine[],
  showBuyers: boolean,
): { subject: string; body: string } {
  const first = lines[0]?.description ?? "your offer";
  const subject =
    lines.length > 1 ? `Enquiry: ${first} + ${lines.length - 1} more` : `Enquiry: ${first}`;
  const name = personName(sellerName);
  const body: string[] = [
    name ? `Hi ${name},` : "Hi,",
    "",
    "We are interested in the following from your offer. Please confirm availability:",
    "",
  ];
  lines.forEach((line, i) => {
    body.push(`${i + 1}. ${line.description}`);
    const price =
      line.unitPrice === null
        ? "Price: please quote your best price"
        : `Price: ${amount(line.unitPrice, line.currency)} per unit`;
    const total = line.total === null ? "" : ` | Total: ${amount(line.total, line.currency)}`;
    body.push(`   Qty: ${line.quantity} pcs | ${price}${total}`);
    for (const note of line.notes.filter((n) => n !== "Price on request")) {
      body.push(`   Note: ${note}`);
    }
    if (showBuyers) body.push(`   For: ${line.suppliers.join(" + ")}`);
    body.push("");
  });
  const sums = totals(lines);
  if (lines.length > 1 && sums.length > 0) {
    body.push(`Total: ${sums.map((s) => amount(s.total, s.currency)).join(" + ")}`, "");
  }
  body.push(
    "Please also confirm the specification, condition and lead time.",
    "",
    "Best regards,",
    "TVD Services",
  );
  return { subject, body: body.join("\n") };
}
