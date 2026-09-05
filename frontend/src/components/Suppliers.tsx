import { useEffect, useState } from "react";

import { fetchSuppliers, updateSupplier, type Supplier } from "../lib/api";
import { money } from "../lib/money";

/** The API joins price and code into one string; split it so it can be shown properly. */
function pretty(sample: string): string {
  const [amount, code] = sample.split(/\s+/);
  const value = Number(amount);
  return Number.isFinite(value) ? money(value, code) : sample;
}

/**
 * Per-supplier parsing settings.
 *
 * Two of these are not preferences. `decimal_separator` decides whether Automic's
 * '1.079' is a thousand euros or one; `default_currency` decides whether a bare '$' is
 * USD or AED. Neither can be worked out from the text — both conventions turn up in the
 * same inbox in the same week.
 *
 * Which is why each row shows that supplier's own recent prices. "Does this one write
 * commas or dots?" is unanswerable in the abstract and obvious with five of their
 * numbers in front of you, and a screen that asks a question it gives you no way to
 * answer gets filled in wrongly or not at all.
 */
export function Suppliers() {
  const [items, setItems] = useState<Supplier[]>([]);
  const [currencies, setCurrencies] = useState<string[]>([]);
  const [unconfigured, setUnconfigured] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const data = await fetchSuppliers();
        setItems(data.items);
        setCurrencies(data.currencies);
        setUnconfigured(data.unconfigured);
      } catch (problem) {
        setError(problem instanceof Error ? problem.message : String(problem));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading) return <p className="muted">Loading suppliers…</p>;
  if (error) return <p className="error">{error}</p>;
  if (items.length === 0) {
    return <p className="empty">No suppliers yet. They appear as their emails arrive.</p>;
  }

  return (
    <>
      {unconfigured > 0 && (
        <p className="warn queue-summary">
          {unconfigured} of {items.length} suppliers have neither a currency nor a number
          format set. Their prices are being read on a guess — and a currency read wrongly
          does not look like an error, it looks like a good margin.
        </p>
      )}

      <table className="offers suppliers">
        <thead>
          <tr>
            <th>Supplier</th>
            <th className="num">Live</th>
            <th>Their recent prices</th>
            <th>Currency</th>
            <th>Number format</th>
            <th>Goes stale after</th>
          </tr>
        </thead>
        <tbody>
          {items.map((supplier) => (
            <SupplierRow key={supplier.id} supplier={supplier} currencies={currencies} />
          ))}
        </tbody>
      </table>
    </>
  );
}

function SupplierRow({
  supplier,
  currencies,
}: {
  supplier: Supplier;
  currencies: string[];
}) {
  const [row, setRow] = useState(supplier);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function save(patch: Partial<Supplier>) {
    setSaving(true);
    setProblem(null);
    try {
      // The server's copy replaces the local one rather than merging into it. If a
      // value was rejected or normalised, the screen should show what is stored, not
      // what was typed.
      setRow(await updateSupplier(supplier.id, patch));
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2000);
    } catch (failure) {
      setProblem(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setSaving(false);
    }
  }

  return (
    <tr>
      <td>
        <div>{row.name || row.primary_email}</div>
        {row.name && <div className="muted small">{row.primary_email}</div>}
        {problem && <div className="error small">{problem}</div>}
        {saved && <div className="resolved small">saved</div>}
      </td>

      <td className="num">{row.live_offers}</td>

      {/* The evidence. Without it the two dropdowns are a quiz with no reference. */}
      <td className="samples">
        {row.sample_prices.length > 0 ? (
          row.sample_prices.map(pretty).join("  ·  ")
        ) : (
          <span className="muted">—</span>
        )}
      </td>

      <td>
        <select
          value={row.default_currency ?? ""}
          disabled={saving}
          onChange={(event) => void save({ default_currency: event.target.value })}
        >
          <option value="">not set</option>
          {currencies.map((code) => (
            <option key={code} value={code}>
              {code}
            </option>
          ))}
        </select>
      </td>

      <td>
        <select
          value={row.decimal_separator ?? ""}
          disabled={saving}
          onChange={(event) =>
            void save({ decimal_separator: event.target.value as "comma" | "dot" | "" as never })
          }
        >
          <option value="">not set</option>
          <option value="comma">1.079,50 — comma is the decimal</option>
          <option value="dot">1,079.50 — dot is the decimal</option>
        </select>
      </td>

      <td>
        <select
          value={row.staleness_hours ?? ""}
          disabled={saving}
          onChange={(event) =>
            void save({
              staleness_hours: event.target.value === "" ? null : Number(event.target.value),
            })
          }
        >
          <option value="">tenant default</option>
          <option value="12">12 hours</option>
          <option value="24">24 hours</option>
          <option value="48">48 hours</option>
          <option value="72">72 hours</option>
          <option value="168">a week</option>
        </select>
      </td>
    </tr>
  );
}
