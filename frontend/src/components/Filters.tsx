import type { OfferFilters } from "../hooks/useOffers";

interface Props {
  filters: OfferFilters;
  brands: string[];
  categories: string[];
  onChange: (filters: OfferFilters) => void;
  total: number;
  showing: number;
}

export function Filters({ filters, brands, categories, onChange, total, showing }: Props) {
  function set<K extends keyof OfferFilters>(key: K, value: OfferFilters[K]) {
    onChange({ ...filters, [key]: value });
  }

  // Arriving from a match pins the board to one lot. Without a way out of it the tab
  // looks broken — one row, and every filter apparently doing nothing.
  if (filters.offerId) {
    return (
      <div className="filters">
        <span className="tag">showing one lot from a match</span>
        <button className="link" onClick={() => onChange({ ...filters, offerId: "" })}>
          show the whole board
        </button>
      </div>
    );
  }

  return (
    <div className="filters">
      <input
        className="search"
        type="search"
        placeholder="Search description, model or EAN…"
        value={filters.search}
        onChange={(e) => set("search", e.target.value)}
      />

      <select value={filters.brand} onChange={(e) => set("brand", e.target.value)}>
        <option value="">All brands</option>
        {brands.map((brand) => (
          <option key={brand} value={brand}>
            {brand}
          </option>
        ))}
      </select>

      <select value={filters.category} onChange={(e) => set("category", e.target.value)}>
        <option value="">All categories</option>
        {categories.map((category) => (
          <option key={category} value={category}>
            {category}
          </option>
        ))}
      </select>

      <label className="checkbox">
        <input
          type="checkbox"
          checked={filters.onlyLive}
          onChange={(e) => set("onlyLive", e.target.checked)}
        />
        Live only
      </label>

      <span className="count">
        {showing.toLocaleString()}
        {total > showing && ` of ${total.toLocaleString()}`} rows
      </span>
    </div>
  );
}
