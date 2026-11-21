import { useEffect, useMemo, useState } from "react";

import { findMatches, type Matches, type MatchOption, type OptionNote } from "../lib/api";
import {
  DIMENSION_LABEL,
  flatten,
  passesSearch,
  passesVariants,
  sortOptions,
  specLine,
  TYPE_LABEL,
  typeCounts,
  typeOf,
  unitPrice,
  variantFacets,
  type PriceSort,
  type VariantDimension,
  type VariantSelection,
} from "../lib/matchList";
import { money } from "../lib/money";
import type { Offer } from "../lib/types";

/**
 * The screen the product exists for.
 *
 * A filterable list is something a trader could build in a spreadsheet. What he cannot
 * do by hand is notice, across two hundred rows from eleven suppliers, that nobody has
 * the hundred units a buyer wants but three suppliers together do.
 */
/**
 * Only the fields the drawer reads.
 *
 * Narrower than `Offer` on purpose: the Matches board opens this from a row it matched,
 * not from a row loaded into the table, and demanding a full Offer would mean fetching
 * one just to show a heading.
 */
/**
 * The markup the client quotes at when a buyer has not named a price.
 *
 * Most buyers do not: a WTB list says what they want and asks to be quoted. That leaves
 * margin genuinely unknown, and the system must not invent the buyer's number. But the
 * *client's* number is not unknown at all — it is his decision, and it is the one half
 * of the trade he controls. So he sets the markup and the quote is arithmetic.
 *
 * Kept per browser rather than per tenant for now: it is a working preference a trader
 * changes between deals, not a fact about the business.
 */
const MARKUP_KEY = "tvd.quote.markup";

function savedMarkup(): number {
  try {
    const raw = window.localStorage.getItem(MARKUP_KEY);
    const value = raw === null ? NaN : Number(raw);
    return Number.isFinite(value) && value >= 0 && value <= 100 ? value : 3;
  } catch {
    return 3;
  }
}

const LAYOUT_KEY = "tvd.matches.layout";

function savedLayout(): "grouped" | "flat" {
  try {
    return window.localStorage.getItem(LAYOUT_KEY) === "flat" ? "flat" : "grouped";
  } catch {
    return "grouped";
  }
}

export type MatchSubject = Pick<
  Offer,
  "id" | "side" | "description" | "quantity" | "unit_price" | "currency"
>;

export function MatchDrawer({
  offer,
  onClose,
  onShowOffer,
  onBuyer,
  onSeller,
}: {
  offer: MatchSubject;
  onClose: () => void;
  onShowOffer?: (offerId: string, side: "sell" | "buy") => void;
  /** Open one buyer: everything they want and every match for it. */
  onBuyer?: (buyerId: string) => void;
  /** Open one seller: every lot they have and who could buy it. */
  onSeller?: (sellerId: string) => void;
}) {
  const [matches, setMatches] = useState<Matches | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const direction = offer.side === "buy" ? "fill" : "place";
  const [markup, setMarkup] = useState(savedMarkup);

  function changeMarkup(value: number) {
    setMarkup(value);
    try {
      window.localStorage.setItem(MARKUP_KEY, String(value));
    } catch {
      // A browser refusing storage is not a reason to stop quoting.
    }
  }

  // How the list is worked: chips, search, sort, grouped or one list.
  const [selection, setSelection] = useState<VariantSelection>({});
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<PriceSort>("best");
  const [layout, setLayout] = useState<"grouped" | "flat">(savedLayout);

  function changeLayout(value: "grouped" | "flat") {
    setLayout(value);
    try {
      window.localStorage.setItem(LAYOUT_KEY, value);
    } catch {
      // Not remembering the choice is fine.
    }
  }

  function toggleChip(dimension: VariantDimension, value: string) {
    setSelection((current) => {
      const chosen = current[dimension] ?? [];
      const next = chosen.includes(value) ? chosen.filter((v) => v !== value) : [...chosen, value];
      return { ...current, [dimension]: next };
    });
  }

  const all = useMemo(() => (matches ? flatten(matches.groups) : []), [matches]);
  const facets = useMemo(() => variantFacets(all), [all]);
  const shown = useMemo(
    () => all.filter((o) => passesVariants(o, selection) && passesSearch(o, query)),
    [all, selection, query],
  );
  const counts = typeCounts(shown);
  const filtering = query.trim() !== "" || Object.values(selection).some((v) => v && v.length);

  // What is drawn, in order, with the serial each row gets. Numbering runs top to
  // bottom across sections and is redone on every sort or filter.
  const sections = useMemo(() => {
    if (!matches) return [];
    const visible = new Set(shown);
    const list: { title: string | null; options: MatchOption[] }[] =
      layout === "flat"
        ? [{ title: null, options: sortOptions(shown, sort, matches.direction) }]
        : Object.entries(matches.groups).map(([title, options]) => ({
            title,
            options: sortOptions(options.filter((o) => visible.has(o)), sort, matches.direction),
          }));
    return list.filter((section) => section.options.length > 0);
  }, [matches, shown, layout, sort]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    findMatches(offer.id, direction)
      .then((result) => !cancelled && setMatches(result))
      .catch((e: Error) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));

    return () => {
      cancelled = true;
    };
  }, [offer.id, direction]);

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer wide" onClick={(e) => e.stopPropagation()}>
        <header>
          <p className="muted small">
            {direction === "fill" ? "Filling a buyer requirement" : "Placing a lot"}
          </p>
          <button className="close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        <div className="subject-block">
          <h2>{offer.description}</h2>
          {matches?.subject_counterparty_id && (
            <p className="subject-party">
              <span className="muted small">{direction === "fill" ? "Buyer" : "Seller"}</span>{" "}
              {(direction === "fill" ? onBuyer : onSeller) ? (
                <button
                  className="link buyer-link"
                  onClick={() =>
                    (direction === "fill" ? onBuyer : onSeller)!(matches.subject_counterparty_id as string)
                  }
                  title={
                    direction === "fill"
                      ? "See everything this buyer wants and draft one email"
                      : "See every lot this seller has and draft one email"
                  }
                >
                  {matches.subject_counterparty_name ?? "Unnamed"} →
                </button>
              ) : (
                <strong>{matches.subject_counterparty_name ?? "Unnamed"}</strong>
              )}
            </p>
          )}
          <p>
            {direction === "fill" ? "wants" : "has"}{" "}
            <strong>{offer.quantity ?? "?"} pcs</strong>
            {offer.unit_price !== null && (
              <>
                {direction === "fill" ? " at up to " : " at "}
                <strong>{money(offer.unit_price, offer.currency)}</strong>
              </>
            )}
          </p>
        </div>

        {direction === "fill" && offer.unit_price === null && (
          <div className="quote-bar">
            <span className="muted small">
              This buyer asked to be quoted rather than naming a price. Quote at cost
              plus
            </span>
            <input
              type="number"
              min={0}
              max={100}
              step={0.5}
              value={markup}
              onChange={(event) => changeMarkup(Number(event.target.value))}
            />
            <span className="muted small">%</span>
          </div>
        )}

        {loading && <p className="muted">Searching the board…</p>}
        {error && <p className="error">{error}</p>}

        {matches && matches.total_options === 0 && (
          <p className="empty">
            Nothing on the board matches this, even loosely.
          </p>
        )}

        {matches && matches.total_options > 0 && (
          <div className="ml-tools">
            <div className="ml-counts" aria-label="Matches by type">
              <span className="ml-count full">
                <b>{counts.full}</b> Full
              </span>
              <span className="ml-count partial">
                <b>{counts.partial}</b> Partial
              </span>
              <span className="ml-count near_miss">
                <b>{counts.near_miss}</b> Near miss
              </span>
              {filtering && (
                <span className="muted small">
                  · showing {shown.length} of {all.length}
                </span>
              )}
            </div>

            <div className="ml-controls">
              <input
                className="ml-search"
                type="search"
                placeholder="Search this list…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              <select
                value={sort}
                onChange={(e) => setSort(e.target.value as PriceSort)}
                aria-label="Sort"
              >
                <option value="best">Sort: best match</option>
                <option value="price_asc">Price: low to high</option>
                <option value="price_desc">Price: high to low</option>
              </select>
              <div className="seg" role="group" aria-label="Layout">
                <button
                  className={layout === "grouped" ? "on" : ""}
                  onClick={() => changeLayout("grouped")}
                >
                  Grouped
                </button>
                <button
                  className={layout === "flat" ? "on" : ""}
                  onClick={() => changeLayout("flat")}
                >
                  Whole list
                </button>
              </div>
            </div>

            {facets.length > 0 && (
              <div className="ml-facets">
                {facets.map((facet) => (
                  <div key={facet.dimension} className="ml-facet">
                    <span className="ml-facet-label">{DIMENSION_LABEL[facet.dimension]}</span>
                    {facet.values.map(({ value, count }) => {
                      const on = (selection[facet.dimension] ?? []).includes(value);
                      return (
                        <button
                          key={value}
                          className={`variant-chip${on ? " on" : ""}`}
                          onClick={() => toggleChip(facet.dimension, value)}
                          aria-pressed={on}
                        >
                          {value} <span className="n">{count}</span>
                        </button>
                      );
                    })}
                  </div>
                ))}
                {filtering && (
                  <button
                    className="link small"
                    onClick={() => {
                      setSelection({});
                      setQuery("");
                    }}
                  >
                    Clear filters
                  </button>
                )}
              </div>
            )}
          </div>
        )}

        {matches && matches.total_options > 0 && shown.length === 0 && (
          <p className="empty">No match in this list has the chosen variants.</p>
        )}

        {(() => {
          let serial = 0;
          return sections.map((section, i) => (
            <section key={section.title ?? i} className="match-group">
              {section.title && <h3>{section.title}</h3>}
              {section.options.map((option) => {
                serial += 1;
                return (
                  <Option
                    key={option.rank ?? serial}
                    serial={serial}
                    option={option}
                    direction={matches!.direction}
                    markup={markup}
                    onShowOffer={onShowOffer}
                    onBuyer={onBuyer}
                    onSeller={onSeller}
                  />
                );
              })}
            </section>
          ));
        })()}
      </aside>
    </div>
  );
}

function Option({
  option,
  serial,
  direction,
  markup,
  onShowOffer,
  onBuyer,
  onSeller,
}: {
  option: MatchOption;
  serial: number;
  direction: string;
  markup: number;
  onShowOffer?: (offerId: string, side: "sell" | "buy") => void;
  onBuyer?: (buyerId: string) => void;
  onSeller?: (sellerId: string) => void;
}) {
  const openLeg = direction === "place" ? onBuyer : onSeller;
  const type = typeOf(option);
  const price = unitPrice(option, direction);
  // Filling a requirement means every leg is a seller's lot; placing a lot means every
  // leg is a buyer's. The tab to open follows from the direction, not from the row.
  const legSide: "sell" | "buy" = direction === "fill" ? "sell" : "buy";
  return (
    <div className={`option ${option.shortfall ? "short" : ""}`}>
      <div className="option-head">
        <span className="fill">
          <span className="sr" title="Sr no">{serial}</span>
          <span className={`type-pill ${type}`}>{TYPE_LABEL[type]}</span>
          {option.filled_quantity}
          {option.requested_quantity ? ` of ${option.requested_quantity}` : ""} units
          {option.supplier_count > 1 && (
            <span className="tag">{option.supplier_count} suppliers</span>
          )}
          {option.shortfall > 0 && (
            <span className="tag closed">short {option.shortfall}</span>
          )}
        </span>

        {price !== null && (
          <span className="unit-price" title="Per unit, as the list is sorted">
            {money(price, option.allocations[0]?.currency)}
            <span className="muted small"> /unit</span>
          </span>
        )}
        <span className={option.total_margin === null ? "muted" : "margin"}>
          {option.total_margin === null
            ? "margin unknown"
            : `${money(option.total_margin)}${
                option.margin_pct !== null ? ` · ${option.margin_pct.toFixed(1)}%` : ""
              }`}
        </span>
      </div>

      <table className="legs">
        <tbody>
          {option.allocations.map((a, i) => (
            <tr key={i}>
              <td className="num">{a.quantity}</td>
              <td className="who">
                {openLeg ? (
                  <button
                    className="link buyer-link"
                    onClick={() => openLeg(a.counterparty_id)}
                    title={
                      direction === "place"
                        ? "See everything this buyer wants and draft one email"
                        : "See every lot this seller has and draft one email"
                    }
                  >
                    {a.counterparty_name ?? a.counterparty_id}
                  </button>
                ) : (
                  (a.counterparty_name ?? a.counterparty_id)
                )}
              </td>
              <td className="desc" title={a.description}>
                {onShowOffer ? (
                  <button
                    className="link leg-link"
                    onClick={() => onShowOffer(a.offer_id, legSide)}
                    title={`Show this lot on the ${legSide === "sell" ? "Sellers" : "Buyers"} tab`}
                  >
                    {a.description}
                  </button>
                ) : (
                  a.description
                )}
                {specLine(a) && <div className="leg-spec">{specLine(a)}</div>}
              </td>
              <td className="num price">{money(a.unit_price, a.currency)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <p className="muted small">
        {direction === "fill" ? "blended cost" : "cost"} {money(option.blended_unit_cost)}
        {option.counterpart_unit_price !== null &&
          ` · ${direction === "fill" ? "buyer pays" : "average sale"} ${money(
            option.counterpart_unit_price,
          )}`}
      </p>

      {/* What to actually send back. The buyer's price is unknown, the cost is not, and
          the markup is the client's own decision — so this is arithmetic rather than a
          guess, and it is the line he pastes into a reply. */}
      {option.total_margin === null &&
        option.blended_unit_cost !== null &&
        option.filled_quantity > 0 && (
          <div className="quote">
            <div>
              <span className="quote-label">Quote</span>{" "}
              <strong>
                {money(
                  option.blended_unit_cost * (1 + markup / 100),
                  option.allocations[0]?.currency,
                )}
              </strong>{" "}
              <span className="muted small">per unit × {option.filled_quantity}</span>
            </div>
            <div className="quote-total">
              {money(
                option.blended_unit_cost * (1 + markup / 100) * option.filled_quantity,
              )}{" "}
              <span className="muted small">
                · earns{" "}
                {money(
                  option.blended_unit_cost * (markup / 100) * option.filled_quantity,
                )}
              </span>
            </div>
          </div>
        )}

      {/* Relaxations and warnings are shown, never hidden. A near-miss is a phone call,
          and a cross-border combination is a margin that freight may eat. */}
      <OptionDetails option={option} direction={direction} />
    </div>
  );
}

const NOTE_GROUPS: { kind: OptionNote["kind"]; title: string }[] = [
  { kind: "check", title: "Check before quoting" },
  { kind: "risk", title: "Deal risk" },
  { kind: "margin", title: "Margin" },
];

function sentence(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

/**
 * What differs, and what to know, laid out rather than listed.
 *
 * Three different kinds of thing used to arrive as one run of orange sentences: how a
 * near miss differs from the order, what to settle about the lot before quoting, and why
 * no margin is shown. Read together they blur — 'capacity NoneGB, wanted 128GB' sat next
 * to 'buyer stated no price' as though both were faults of the lot. So the differences
 * are a two-column comparison, seller's lot against the buyer's order, and every note
 * sits under a heading saying what it is about.
 */
function OptionDetails({ option, direction }: { option: MatchOption; direction: string }) {
  const differences = option.differences ?? [];
  const notes = option.notes ?? [];

  // An older backend sends only the plain lists. Show them as they were.
  if (!option.differences && !option.notes) {
    return (
      <>
        {[...option.relaxed, ...option.warnings].map((note, i) => (
          <p key={i} className="note">
            {note}
          </p>
        ))}
      </>
    );
  }

  const groups = NOTE_GROUPS.map((group) => ({
    ...group,
    items: notes.filter((note) => note.kind === group.kind),
  })).filter((group) => group.items.length > 0);

  if (differences.length === 0 && groups.length === 0) return null;

  return (
    <div className="option-details">
      {differences.length > 0 && (
        <table className="diff">
          <thead>
            <tr>
              <th>Differs</th>
              <th>{direction === "fill" ? "Seller has" : "This lot"}</th>
              <th>Buyer wants</th>
            </tr>
          </thead>
          <tbody>
            {differences.map((d, i) => (
              <tr key={i}>
                <th scope="row">{d.label}</th>
                <td className="offered" title={d.offered}>
                  {d.offered}
                </td>
                <td className="wanted" title={d.wanted}>
                  {d.wanted}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {groups.map((group) => (
        <div key={group.kind} className={`note-group ${group.kind}`}>
          <div className="note-title">{group.title}</div>
          <ul>
            {group.items.map((note, i) => (
              <li key={i}>{sentence(note.text)}</li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
