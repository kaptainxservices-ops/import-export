import type { Allocation, MatchOption, MatchType } from "./api";

/**
 * Working a match list: variants, sorting, the Full / Partial / Near miss split.
 *
 * A match list for 'Samsung A17' can hold 5/128, 6/128 and 8/256 lots side by side.
 * Which details vary differs from product to product — storage for a phone, colour for
 * a case, region for a console — so the variants are read from the rows themselves:
 * a detail becomes a filter only when the rows in this list disagree about it.
 */

export type VariantDimension = "memory" | "colour" | "region" | "network" | "grade" | "edition";

export const DIMENSION_LABEL: Record<VariantDimension, string> = {
  memory: "Memory",
  colour: "Colour",
  region: "Region",
  network: "Network",
  grade: "Grade",
  edition: "Edition",
};

const DIMENSIONS: VariantDimension[] = ["memory", "colour", "region", "network", "grade", "edition"];

export const NOT_STATED = "Not stated";

// Read from the description when extraction left the field empty: '6/128', '6+128GB',
// '8GB/256GB', '128GB', '1TB'. Only used as a fallback; a stated field always wins.
const RAM_STORAGE = /\b(\d{1,2})\s*(?:GB)?\s*[/+]\s*(\d{2,4}|1|2)\s*(GB|TB)?\b/i;
const STORAGE = /\b(\d{2,4}|1|2)\s*(GB|TB)\b/i;
const NETWORK = /\b(5G|4G|LTE|WIFI|WI-FI|CELLULAR)\b/i;

function storageLabel(gb: number): string {
  return gb >= 1024 && gb % 1024 === 0 ? `${gb / 1024}TB` : `${gb}GB`;
}

function memoryOf(a: Allocation): string | null {
  let ram = a.ram_gb ?? null;
  let storage = a.capacity_gb ?? null;
  if (ram === null || storage === null) {
    const both = RAM_STORAGE.exec(a.description ?? "");
    if (both) {
      ram = ram ?? Number(both[1]);
      if (storage === null) {
        const value = Number(both[2]);
        storage = (both[3] ?? "").toUpperCase() === "TB" ? value * 1024 : value;
      }
    } else if (storage === null) {
      const only = STORAGE.exec(a.description ?? "");
      if (only) storage = only[2].toUpperCase() === "TB" ? Number(only[1]) * 1024 : Number(only[1]);
    }
  }
  if (storage === null && ram === null) return null;
  if (storage === null) return `${ram}GB RAM`;
  const size = storageLabel(storage);
  return ram !== null ? `${ram}/${size.replace(/GB$/, "")}` : size;
}

function clean(value: string | null | undefined): string | null {
  const text = (value ?? "").trim();
  return text ? text.toUpperCase() === text && text.length > 3 ? title(text) : text : null;
}

function title(text: string): string {
  return text.toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
}

// Colour names as the trade writes them. A supplier's colour field often carries more
// than the colour -- 'Burgundy OEM', 'Glacier MJXR HX', 'Burgundi QLA', 'Burgunder'
// (German) -- and each would otherwise become its own chip. The first word is matched
// to one of these by its first letters; anything else keeps its first word.
const COLOURS = [
  "black", "white", "silver", "gold", "rose gold", "blue", "light blue", "dark blue", "navy",
  "green", "dark green", "mint", "red", "purple", "deep purple", "lavender", "violet", "pink",
  "burgundy", "glacier", "gray", "grey", "graphite", "titanium", "natural", "desert", "orange",
  "yellow", "midnight", "starlight", "cream", "teal", "ultramarine", "obsidian", "porcelain",
  "hazel", "coral", "lemongrass", "bronze", "copper", "beige", "brown", "cosmic", "space",
];

function colourName(value: string | null | undefined): string | null {
  const text = (value ?? "").trim().toLowerCase().replace(/[^a-z\s]/g, " ").replace(/\s+/g, " ");
  if (!text) return null;
  for (const name of COLOURS) {
    if (name.includes(" ") && text.startsWith(name)) return title(name);
  }
  const word = text.split(" ")[0];
  if (word.length >= 4) {
    for (const name of COLOURS) {
      if (name.includes(" ")) continue;
      const stem = name.slice(0, Math.min(6, name.length));
      if (word === name || (word.length >= stem.length && word.startsWith(stem))) {
        return title(name === "grey" ? "gray" : name);
      }
    }
  }
  return title(word);
}

export function legValue(a: Allocation, dimension: VariantDimension): string | null {
  switch (dimension) {
    case "memory":
      return memoryOf(a);
    case "colour":
      return colourName(a.colour);
    case "region":
      return a.region_code ? a.region_code.toUpperCase() : null;
    case "network": {
      const stated = a.network || a.connectivity;
      if (stated) return stated.toUpperCase();
      const found = NETWORK.exec(a.description ?? "");
      return found ? found[1].toUpperCase().replace("WI-FI", "WIFI") : null;
    }
    case "grade":
      return a.grade ? a.grade.toUpperCase() : null;
    case "edition":
      return clean(a.edition);
  }
}

/** Every value an option has for a dimension: a combination can span several. */
export function optionValues(option: MatchOption, dimension: VariantDimension): string[] {
  const values = new Set<string>();
  for (const a of option.allocations) values.add(legValue(a, dimension) ?? NOT_STATED);
  return [...values];
}

export interface VariantFacet {
  dimension: VariantDimension;
  values: { value: string; count: number }[];
}

/** Only the details the rows disagree about. Two stated values at least. */
export function variantFacets(options: MatchOption[]): VariantFacet[] {
  const facets: VariantFacet[] = [];
  for (const dimension of DIMENSIONS) {
    const counts = new Map<string, number>();
    const seen = new Set<string>();
    for (const option of options) {
      const values = optionValues(option, dimension);
      values.forEach((v) => seen.add(v));
      // Counted only where the chip alone would show it: a combination that is half
      // 6/128 and half 4/128 passes neither chip on its own, so it counts for neither.
      if (values.length === 1) counts.set(values[0], (counts.get(values[0]) ?? 0) + 1);
    }
    for (const value of seen) if (!counts.has(value)) counts.set(value, 0);
    const stated = [...seen].filter((v) => v !== NOT_STATED);
    if (stated.length < 2) continue;
    facets.push({
      dimension,
      values: [...counts.entries()]
        .map(([value, count]) => ({ value, count }))
        .sort((a, b) => (a.value === NOT_STATED ? 1 : b.value === NOT_STATED ? -1 : 0) ||
          naturalCompare(a.value, b.value)),
    });
  }
  return facets;
}

function naturalCompare(a: string, b: string): number {
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
}

export type VariantSelection = Partial<Record<VariantDimension, string[]>>;

/**
 * Whether an option passes the chosen chips. Within one detail any chosen value will
 * do; across details all must hold. Every leg of a combination has to pass: a 6/128
 * filter should not show a combination that is half 4/128.
 */
export function passesVariants(option: MatchOption, selection: VariantSelection): boolean {
  for (const [dimension, chosen] of Object.entries(selection) as [VariantDimension, string[]][]) {
    if (!chosen || chosen.length === 0) continue;
    if (!optionValues(option, dimension).every((v) => chosen.includes(v))) return false;
  }
  return true;
}

export function passesSearch(option: MatchOption, query: string): boolean {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return true;
  const haystack = option.allocations
    .map((a) =>
      [a.description, a.counterparty_name, a.colour, a.region_code, a.grade, memoryOf(a)]
        .filter(Boolean)
        .join(" "),
    )
    .join(" ")
    .toLowerCase();
  return words.every((w) => haystack.includes(w));
}

// ---------------------------------------------------------------- types and counts

export function typeOf(option: MatchOption): MatchType {
  if (option.match_type) return option.match_type;
  if (option.kind === "near_miss") return "near_miss";
  return option.shortfall > 0 ? "partial" : "full";
}

export const TYPE_LABEL: Record<MatchType, string> = {
  full: "Full",
  partial: "Partial",
  near_miss: "Near miss",
};

export function typeCounts(options: MatchOption[]): Record<MatchType, number> {
  const counts: Record<MatchType, number> = { full: 0, partial: 0, near_miss: 0 };
  for (const option of options) counts[typeOf(option)] += 1;
  return counts;
}

// ---------------------------------------------------------------- price and sort

export type PriceSort = "best" | "price_asc" | "price_desc";

/**
 * The per-unit price the list is sorted by. Filling a buyer: what the lot costs (the
 * blended cost for a combination). Placing a lot: what the buyers would pay, weighted
 * by the quantity each takes. Null when no leg is priced.
 */
export function unitPrice(option: MatchOption, direction: string): number | null {
  if (direction === "fill" && option.blended_unit_cost !== null) return option.blended_unit_cost;
  let units = 0;
  let total = 0;
  for (const a of option.allocations) {
    if (a.unit_price === null || a.unit_price === undefined) continue;
    units += a.quantity;
    total += a.unit_price * a.quantity;
  }
  if (units > 0) return total / units;
  return direction === "place" ? option.counterpart_unit_price : null;
}

/** Sorted copy. Unpriced options always go last, whichever way prices run. */
export function sortOptions(options: MatchOption[], sort: PriceSort, direction: string): MatchOption[] {
  const byRank = (a: MatchOption, b: MatchOption) => (a.rank ?? 0) - (b.rank ?? 0);
  const copy = [...options];
  if (sort === "best") return copy.sort(byRank);
  const sign = sort === "price_asc" ? 1 : -1;
  return copy.sort((a, b) => {
    const pa = unitPrice(a, direction);
    const pb = unitPrice(b, direction);
    if (pa === null && pb === null) return byRank(a, b);
    if (pa === null) return 1;
    if (pb === null) return -1;
    return sign * (pa - pb) || byRank(a, b);
  });
}

/**
 * Every option across the groups, the same objects, each once. An older backend sends
 * no rank; the order the groups arrive in stands in for it.
 */
export function flatten(groups: Record<string, MatchOption[]>): MatchOption[] {
  const options = Object.values(groups).flat();
  options.forEach((o, i) => {
    if (o.rank === undefined) o.rank = i;
  });
  return options;
}

/** A short spec line for a lot: '6/128 · Black · EU'. */
export function specLine(a: Allocation): string {
  return (["memory", "colour", "region", "network", "grade"] as VariantDimension[])
    .map((d) => legValue(a, d))
    .filter(Boolean)
    .join(" · ");
}
