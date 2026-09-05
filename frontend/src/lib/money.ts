/**
 * Money, formatted the way a trader reads it.
 *
 * A symbol only where it is unambiguous *to the reader*. The parser has good reason to
 * distrust a bare '$' — it is USD in Dubai, SGD in Singapore, CAD in Toronto — but that
 * ambiguity is about reading an email, not about displaying a value we already know is
 * USD. Once the currency is settled, '$905' is clearer than '905 USD'.
 *
 * Currencies with no symbol anyone would recognise keep their code. 'AED 3,320' reads
 * correctly to everyone in this trade; 'د.إ' in the middle of a Latin table does not.
 */
const SYMBOLS: Record<string, string> = {
  EUR: "€",
  USD: "$",
  GBP: "£",
  JPY: "¥",
  CNY: "¥",
  INR: "₹",
  KRW: "₩",
  RUB: "₽",
};

// These read as a code and are written that way in every price list in the corpus.
const CODE_BEFORE = new Set(["AED", "SAR", "QAR", "KWD", "CHF", "PLN", "CZK", "SEK", "HKD", "SGD"]);

export interface MoneyOptions {
  /** Show fractional units. Off for headline figures, on for prices. */
  decimals?: boolean;
  /** '47.2k' rather than '47,231'. For tiles, never for a price someone will quote. */
  compact?: boolean;
}

export function money(
  value: number | null | undefined,
  currency?: string | null,
  options: MoneyOptions = {},
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";

  const { decimals = true, compact = false } = options;

  const digits = compact
    ? { minimumFractionDigits: 0, maximumFractionDigits: 1 }
    : decimals
      ? { minimumFractionDigits: 2, maximumFractionDigits: 2 }
      : { minimumFractionDigits: 0, maximumFractionDigits: 0 };

  const shown = compact && Math.abs(value) >= 1000 ? value / 1000 : value;
  const suffix = compact && Math.abs(value) >= 1000 ? "k" : "";
  const number = shown.toLocaleString(undefined, digits) + suffix;

  const code = (currency ?? "").toUpperCase();
  if (!code) return number;

  const symbol = SYMBOLS[code];
  if (symbol) return `${symbol}${number}`;
  if (CODE_BEFORE.has(code)) return `${code} ${number}`;

  // An unrecognised code is still shown rather than dropped: a number with no currency
  // beside it is the single most dangerous thing on this board.
  return `${number} ${code}`;
}

/** Whole units, for headline figures. */
export function bigMoney(value: number | null | undefined, currency?: string | null): string {
  return money(value, currency, { compact: true, decimals: false });
}
