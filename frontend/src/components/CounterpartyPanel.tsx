import { useEffect, useMemo, useState } from "react";

import {
  findBuyerMatches,
  findSellerMatches,
  polishEmail,
  type Matches,
  type MatchOption,
  type MatchType,
} from "../lib/api";
import {
  flatten,
  sortOptions,
  specLine,
  TYPE_LABEL,
  typeCounts,
  typeOf,
  unitPrice,
} from "../lib/matchList";
import { money } from "../lib/money";
import {
  amount,
  buildEmail,
  buildLine,
  buildSellerEmail,
  buildSellerLine,
  totals,
  type QuoteLine,
} from "../lib/quoteEmail";

/**
 * One counterparty, everything at once: a buyer and every match for what they want,
 * or a seller and every buyer for what they have.
 *
 * Three tabs. Products lists what they want (or have). Matches splits every option into
 * Full, Partial and None, so the client works one kind of deal at a time and ticks the
 * ones he wants. Selected holds the ticked deals and drafts one email covering them all.
 */

export type Side = "buy" | "sell";

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

// mailto bodies past roughly this length are cut short by Outlook on Windows.
const MAILTO_SAFE = 1800;

type Tab = "products" | "matches" | "selected";
type Kind = "full" | "partial" | "none";

interface Party {
  name: string | null;
  email: string | null;
  items: Matches[];
}

interface Item {
  subject: Matches;
  options: MatchOption[];
}

interface Deal {
  subject: Matches;
  option: MatchOption;
  index: number;
}

export function CounterpartyPanel({
  side,
  partyId,
  onClose,
  onOpenItem,
}: {
  side: Side;
  partyId: string;
  onClose: () => void;
  /** Open one product's full match list. */
  onOpenItem?: (item: Matches) => void;
}) {
  const buyer = side === "buy";
  const words = buyer
    ? { party: "Buyer", item: "Products wanted", short: "product", other: "supplier", none: "Nothing on the board can fill these" }
    : { party: "Seller", item: "Lots", short: "lot", other: "buyer", none: "No buyer wants these right now" };

  const [data, setData] = useState<Party | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [tab, setTab] = useState<Tab>("products");
  const [kind, setKind] = useState<Kind>("full");
  // One ticked option per product, by its rank in that product's list. Two options for
  // one product are alternatives; ticking both would quote the same order twice.
  const [picked, setPicked] = useState<Record<string, number>>({});
  const [composing, setComposing] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setPicked({});
    setComposing(false);
    setTab("products");
    const load = buyer
      ? findBuyerMatches(partyId).then((r) => ({ name: r.name, email: r.email, items: r.requirements }))
      : findSellerMatches(partyId).then((r) => ({ name: r.name, email: r.email, items: r.lots }));
    load
      .then((result) => !cancelled && setData(result))
      .catch((e: Error) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [partyId, buyer]);

  const items: Item[] = useMemo(
    () =>
      (data?.items ?? []).map((subject) => ({
        subject,
        options: sortOptions(flatten(subject.groups), "best", subject.direction),
      })),
    [data],
  );

  const all = items.flatMap((i) => i.options);
  const counts = typeCounts(all);
  const unmatched = items.filter((i) => i.options.length === 0);
  const kindCount: Record<Kind, number> = {
    full: counts.full,
    partial: counts.partial,
    none: counts.near_miss + unmatched.length,
  };

  const selected: Deal[] = items
    .map(({ subject, options }, index) => {
      const rank = picked[subject.subject_id];
      const option = rank === undefined ? undefined : options.find((o) => o.rank === rank);
      return option ? { subject, option, index } : null;
    })
    .filter((x): x is Deal => x !== null);

  function toggle(subjectId: string, rank: number) {
    setPicked((current) => {
      const next = { ...current };
      if (next[subjectId] === rank) delete next[subjectId];
      else next[subjectId] = rank;
      return next;
    });
  }

  function pickBestOfKind(type: MatchType) {
    const next = { ...picked };
    for (const { subject, options } of items) {
      const best = options.find((o) => typeOf(o) === type);
      if (best && next[subject.subject_id] === undefined) next[subject.subject_id] = best.rank ?? 0;
    }
    setPicked(next);
  }

  function goToItem(subjectId: string) {
    const item = items.find((i) => i.subject.subject_id === subjectId);
    const c = item ? typeCounts(item.options) : null;
    setKind(!c || (c.full === 0 && c.partial === 0) ? "none" : c.full > 0 ? "full" : "partial");
    setTab("matches");
    window.setTimeout(
      () =>
        document.getElementById(`cp-item-${subjectId}`)?.scrollIntoView({ behavior: "smooth", block: "start" }),
      60,
    );
  }

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer wide buyer-panel" onClick={(e) => e.stopPropagation()}>
        <header>
          <p className="muted small">{words.party}</p>
          <button className="close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        <div className="subject-block">
          <h2>{data?.name ?? (loading ? "Loading…" : `Unnamed ${words.party.toLowerCase()}`)}</h2>
          {data?.email && <p className="muted">{data.email}</p>}
        </div>

        {loading && <p className="muted">Matching everything at once…</p>}
        {error && <p className="error">{error}</p>}

        {data && (
          <nav className="cp-tabs" role="tablist">
            {(["products", "matches", "selected"] as Tab[]).map((t) => (
              <button
                key={t}
                role="tab"
                aria-selected={tab === t}
                className={tab === t ? "on" : ""}
                onClick={() => {
                  setTab(t);
                  setComposing(false);
                }}
              >
                {t === "products" ? words.item : t === "matches" ? "Matches" : "Selected"}
                <span className="n">
                  {t === "products" ? items.length : t === "matches" ? all.length : selected.length}
                </span>
              </button>
            ))}
          </nav>
        )}

        {data && tab === "products" && (
          <section className="bp-section">
            {items.length === 0 ? (
              <p className="empty">Nothing live from this {words.party.toLowerCase()} this week.</p>
            ) : (
              <table className="bp-products">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>{buyer ? "Product" : "Lot"}</th>
                    <th className="num">Qty</th>
                    <th className="num">{buyer ? "Target" : "Price"}</th>
                    <th>Matches</th>
                    <th>Selected</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map(({ subject, options }, i) => {
                    const c = typeCounts(options);
                    return (
                      <tr key={subject.subject_id} onClick={() => goToItem(subject.subject_id)}>
                        <td className="num">{i + 1}</td>
                        <td>{subject.subject_description}</td>
                        <td className="num">{subject.subject_quantity ?? "—"}</td>
                        <td className="num">
                          {subject.subject_price === null ? (
                            <span className="muted">{buyer ? "quote" : "on request"}</span>
                          ) : (
                            money(subject.subject_price, subject.subject_currency)
                          )}
                        </td>
                        <td>
                          {options.length === 0 ? (
                            <span className="muted">none</span>
                          ) : (
                            <span className="bp-mini-counts">
                              {c.full > 0 && <span className="type-pill full">{c.full} Full</span>}
                              {c.partial > 0 && <span className="type-pill partial">{c.partial} Partial</span>}
                              {c.near_miss > 0 && <span className="type-pill near_miss">{c.near_miss} Near</span>}
                            </span>
                          )}
                        </td>
                        <td>
                          {picked[subject.subject_id] !== undefined ? (
                            <span className="bp-tick">✓</span>
                          ) : (
                            <span className="muted">—</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </section>
        )}

        {data && tab === "matches" && (
          <section className="bp-section">
            <div className="cp-kinds">
              {(["full", "partial", "none"] as Kind[]).map((k) => (
                <button
                  key={k}
                  className={`cp-kind ${k}${kind === k ? " on" : ""}`}
                  onClick={() => setKind(k)}
                >
                  {k === "full" ? "Full" : k === "partial" ? "Partial" : "None"}
                  <span className="n">{kindCount[k]}</span>
                </button>
              ))}
            </div>
            <p className="muted small">
              {kind === "full"
                ? `Covers the whole quantity at the asked spec.`
                : kind === "partial"
                  ? `Covers part of the quantity.`
                  : buyer
                    ? "Near misses (a different spec), then products nothing on the board can fill."
                    : "Lots no buyer currently wants."}{" "}
              Tick one deal per {words.short}; ticking another for the same {words.short} replaces it.
            </p>
            {kind !== "none" && kindCount[kind] > 0 && (
              <button className="link small" onClick={() => pickBestOfKind(kind)}>
                Tick the best {kind} match for each {words.short}
              </button>
            )}

            {kind === "none" ? (
              <>
                {items
                  .filter(({ options }) => options.some((o) => typeOf(o) === "near_miss"))
                  .map(({ subject, options }) => (
                    <ItemBlock
                      key={subject.subject_id}
                      serial={items.findIndex((i) => i.subject.subject_id === subject.subject_id) + 1}
                      subject={subject}
                      options={options.filter((o) => typeOf(o) === "near_miss")}
                      picked={picked[subject.subject_id]}
                      onToggle={toggle}
                      onOpen={buyer ? onOpenItem : undefined}
                      other={words.other}
                    />
                  ))}
                {unmatched.length > 0 && (
                  <div className="cp-unmatched">
                    <h4>{words.none}</h4>
                    <ul>
                      {unmatched.map(({ subject }) => (
                        <li key={subject.subject_id}>
                          <span>{subject.subject_description}</span>
                          <span className="muted small">
                            {" "}
                            · {subject.subject_quantity ?? "?"} pcs
                            {subject.subject_price !== null &&
                              ` · ${money(subject.subject_price, subject.subject_currency)}`}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {kindCount.none === 0 && <p className="empty">Nothing here.</p>}
              </>
            ) : (
              <>
                {items
                  .filter(({ options }) => options.some((o) => typeOf(o) === kind))
                  .map(({ subject, options }) => (
                    <ItemBlock
                      key={subject.subject_id}
                      serial={items.findIndex((i) => i.subject.subject_id === subject.subject_id) + 1}
                      subject={subject}
                      options={options.filter((o) => typeOf(o) === kind)}
                      picked={picked[subject.subject_id]}
                      onToggle={toggle}
                      onOpen={buyer ? onOpenItem : undefined}
                      other={words.other}
                    />
                  ))}
                {kindCount[kind] === 0 && <p className="empty">No {kind} matches.</p>}
              </>
            )}

            <div className="bp-footer">
              <span>
                <b>{selected.length}</b> deal{selected.length === 1 ? "" : "s"} selected
              </span>
              <button className="resolve" disabled={selected.length === 0} onClick={() => setTab("selected")}>
                Review selected →
              </button>
            </div>
          </section>
        )}

        {data && tab === "selected" && !composing && (
          <section className="bp-section">
            {selected.length === 0 ? (
              <p className="empty">
                No deals ticked yet. Open the Matches tab and tick the deals you want.
              </p>
            ) : (
              selected.map(({ subject, option, index }) => (
                <div key={subject.subject_id} className="cp-selected">
                  <div className="bp-req-head">
                    <span className="sr">{index + 1}</span>
                    <strong>{subject.subject_description}</strong>
                    <span className="muted small">
                      {buyer ? "wants" : "has"} {subject.subject_quantity ?? "?"} pcs
                    </span>
                    <button
                      className="link small danger"
                      onClick={() => toggle(subject.subject_id, option.rank ?? 0)}
                    >
                      Remove
                    </button>
                  </div>
                  <PickRow serial={1} option={option} checked onToggle={() => toggle(subject.subject_id, option.rank ?? 0)} other={words.other} direction={subject.direction} hideSerial />
                </div>
              ))
            )}
            <div className="bp-footer">
              <span>
                <b>{selected.length}</b> deal{selected.length === 1 ? "" : "s"} for{" "}
                {data.name ?? `this ${words.party.toLowerCase()}`}
              </span>
              <span className="bp-footer-actions">
                {selected.length > 0 && (
                  <button className="link" onClick={() => setPicked({})}>
                    Clear all
                  </button>
                )}
                <button className="resolve" disabled={selected.length === 0} onClick={() => setComposing(true)}>
                  Draft email
                </button>
              </span>
            </div>
          </section>
        )}

        {data && tab === "selected" && composing && (
          <Composer
            side={side}
            partyName={data.name}
            partyEmail={data.email}
            deals={selected}
            onBack={() => setComposing(false)}
          />
        )}
      </aside>
    </div>
  );
}

function ItemBlock({
  serial,
  subject,
  options,
  picked,
  onToggle,
  onOpen,
  other,
}: {
  serial: number;
  subject: Matches;
  options: MatchOption[];
  picked: number | undefined;
  onToggle: (subjectId: string, rank: number) => void;
  onOpen?: (item: Matches) => void;
  other: string;
}) {
  return (
    <div id={`cp-item-${subject.subject_id}`} className="bp-req">
      <div className="bp-req-head">
        <span className="sr">{serial}</span>
        <strong>{subject.subject_description}</strong>
        <span className="muted small">
          {subject.direction === "fill" ? "wants" : "has"} {subject.subject_quantity ?? "?"} pcs
          {subject.subject_price !== null &&
            ` · ${subject.direction === "fill" ? "up to " : ""}${money(subject.subject_price, subject.subject_currency)}`}
        </span>
        {onOpen && (
          <button className="link small" onClick={() => onOpen(subject)}>
            Open full match list
          </button>
        )}
      </div>
      {options.map((option, n) => (
        <PickRow
          key={option.rank ?? n}
          serial={n + 1}
          option={option}
          checked={picked === option.rank}
          onToggle={() => onToggle(subject.subject_id, option.rank ?? 0)}
          other={other}
          direction={subject.direction}
        />
      ))}
    </div>
  );
}

function PickRow({
  serial,
  option,
  checked,
  onToggle,
  other,
  direction,
  hideSerial,
}: {
  serial: number;
  option: MatchOption;
  checked: boolean;
  onToggle: () => void;
  other: string;
  /** fill: the price is what the lot costs. place: what the buyers pay. */
  direction: string;
  hideSerial?: boolean;
}) {
  const type = typeOf(option);
  const price = unitPrice(option, direction);
  return (
    <label className={`bp-pick${checked ? " on" : ""}`}>
      <input type="checkbox" checked={checked} onChange={onToggle} />
      {!hideSerial && <span className="sr">{serial}</span>}
      <span className={`type-pill ${type}`}>{TYPE_LABEL[type]}</span>
      <span className="bp-pick-main">
        <span className="bp-pick-legs">
          {option.allocations.map((a, i) => (
            <span key={i} className="bp-leg">
              <b>{a.quantity}</b> × {a.description}
              {specLine(a) && <span className="muted"> · {specLine(a)}</span>}
              <span className="muted">
                {" "}
                — {a.counterparty_name ?? `unnamed ${other}`}
                {a.unit_price !== null && ` @ ${money(a.unit_price, a.currency)}`}
              </span>
            </span>
          ))}
        </span>
        {option.kind === "near_miss" && (option.differences ?? []).length > 0 && (
          <span className="bp-diff">
            {(option.differences ?? []).map((d) => `${d.label}: ${d.offered} vs ${d.wanted}`).join(" · ")}
          </span>
        )}
      </span>
      <span className="bp-pick-fig">
        <span>
          {option.filled_quantity}
          {option.requested_quantity ? ` / ${option.requested_quantity}` : ""} pcs
        </span>
        <span className="muted small">
          {price === null ? "no price" : `${money(price, option.allocations[0]?.currency)} /unit`}
        </span>
        {option.total_margin !== null && (
          <span className="margin small">+{money(option.total_margin)}</span>
        )}
      </span>
    </label>
  );
}

// ---------------------------------------------------------------- the email

function Composer({
  side,
  partyName,
  partyEmail,
  deals,
  onBack,
}: {
  side: Side;
  partyName: string | null;
  partyEmail: string | null;
  deals: Deal[];
  onBack: () => void;
}) {
  const buyer = side === "buy";
  const [margin, setMargin] = useState(savedMarkup);
  const [overrides, setOverrides] = useState<Record<string, number>>({});
  const [showOther, setShowOther] = useState(false);
  const [to, setTo] = useState(partyEmail ?? "");

  const lines: QuoteLine[] = useMemo(
    () =>
      deals.map(({ subject, option }) =>
        buyer
          ? buildLine(subject, option, overrides[subject.subject_id] ?? margin)
          : buildSellerLine(subject, option),
      ),
    [deals, overrides, margin, buyer],
  );
  const generated = useMemo(
    () => (buyer ? buildEmail(partyName, lines, showOther) : buildSellerEmail(partyName, lines, showOther)),
    [buyer, partyName, lines, showOther],
  );

  const [subject, setSubject] = useState(generated.subject);
  const [body, setBody] = useState(generated.body);
  // Once the client types into the email, a change of margin no longer overwrites it;
  // he is told the figures moved and chooses when to rebuild.
  const [edited, setEdited] = useState(false);
  const [basis, setBasis] = useState(generated.body);
  const stale = edited && generated.body !== basis;

  useEffect(() => {
    if (!edited) {
      setBody(generated.body);
      setSubject(generated.subject);
      setBasis(generated.body);
    }
  }, [generated, edited]);

  const [polishing, setPolishing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [undo, setUndo] = useState<string | null>(null);

  function changeMargin(value: number) {
    const v = Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 0;
    setMargin(v);
    try {
      window.localStorage.setItem(MARKUP_KEY, String(v));
    } catch {
      // fine
    }
  }

  function regenerate() {
    setBody(generated.body);
    setSubject(generated.subject);
    setEdited(false);
    setUndo(null);
    setNotice(null);
  }

  async function copy(text: string, what: string) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const area = document.createElement("textarea");
      area.value = text;
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    setNotice(`${what} copied.`);
    window.setTimeout(() => setNotice(null), 2000);
  }

  async function polish() {
    setPolishing(true);
    setNotice(null);
    try {
      const better = await polishEmail(body);
      setUndo(body);
      setBody(better);
      setEdited(true);
      setNotice("Wording polished. Every price and quantity was checked unchanged.");
    } catch (e) {
      setNotice((e as Error).message);
    } finally {
      setPolishing(false);
    }
  }

  const mailto = `mailto:${encodeURIComponent(to)}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
  const tooLong = mailto.length > MAILTO_SAFE;
  const sums = totals(lines);
  const costSums = new Map<string, number>();
  for (const l of lines) {
    if (l.cost === null) continue;
    const key = (l.currency ?? "").toUpperCase();
    costSums.set(key, (costSums.get(key) ?? 0) + l.cost * l.quantity);
  }

  return (
    <div className="composer">
      <button className="link small" onClick={onBack}>
        ← Back to selected
      </button>

      <section className="bp-section">
        <h3>{buyer ? "Pricing" : "What you are asking for"}</h3>
        <div className="quote-bar">
          {buyer && (
            <>
              <span className="muted small">Margin on every line</span>
              <input
                type="number"
                min={0}
                max={100}
                step={0.5}
                value={margin}
                onChange={(e) => changeMargin(Number(e.target.value))}
              />
              <span className="muted small">%</span>
            </>
          )}
          <label className="cmp-toggle">
            <input type="checkbox" checked={showOther} onChange={(e) => setShowOther(e.target.checked)} />{" "}
            Show {buyer ? "supplier" : "buyer"} names in the email
          </label>
        </div>

        <table className="cmp-lines">
          <thead>
            <tr>
              <th>#</th>
              <th>{buyer ? "Product" : "Lot"}</th>
              <th className="num">Qty</th>
              <th className="num">{buyer ? "Cost /unit" : "Their price"}</th>
              {buyer && <th className="num">Margin %</th>}
              {buyer && <th className="num">Quote /unit</th>}
              <th className="num">Line total</th>
            </tr>
          </thead>
          <tbody>
            {lines.map((line, i) => (
              <tr key={line.requirementId}>
                <td className="num">{i + 1}</td>
                <td>
                  {line.description}
                  {line.notes.length > 0 && <div className="muted small">{line.notes.join(" · ")}</div>}
                  {!line.currency && line.unitPrice !== null && (
                    <div className="cmp-warn">
                      No currency was stated. Add it to the email before sending.
                    </div>
                  )}
                  <div className="muted small">
                    {buyer ? "from" : "for"} {line.suppliers.join(" + ")}
                  </div>
                </td>
                <td className="num">{line.quantity}</td>
                <td className="num">{line.cost === null ? "—" : amount(line.cost, line.currency)}</td>
                {buyer && (
                  <td className="num">
                    <input
                      className={`cmp-margin${overrides[line.requirementId] !== undefined ? " own" : ""}`}
                      type="number"
                      min={0}
                      max={100}
                      step={0.5}
                      value={line.marginPct}
                      onChange={(e) =>
                        setOverrides((o) => ({ ...o, [line.requirementId]: Number(e.target.value) }))
                      }
                      title="This line's own margin. Reset to follow the overall margin."
                    />
                    {overrides[line.requirementId] !== undefined && (
                      <button
                        className="link small"
                        onClick={() =>
                          setOverrides((o) => {
                            const next = { ...o };
                            delete next[line.requirementId];
                            return next;
                          })
                        }
                      >
                        reset
                      </button>
                    )}
                  </td>
                )}
                {buyer && (
                  <td className="num">
                    <b>{line.unitPrice === null ? "on request" : amount(line.unitPrice, line.currency)}</b>
                  </td>
                )}
                <td className="num">{line.total === null ? "—" : amount(line.total, line.currency)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td colSpan={buyer ? 6 : 4} className="num muted">
                {buyer ? "Quote total · you earn" : "Total"}
              </td>
              <td className="num">
                {sums.map((s) => (
                  <div key={s.currency ?? ""}>
                    <b>{amount(s.total, s.currency)}</b>
                    {buyer && (
                      <span className="muted small">
                        {" "}
                        · {amount(s.total - (costSums.get((s.currency ?? "").toUpperCase()) ?? 0), s.currency)}
                      </span>
                    )}
                  </div>
                ))}
              </td>
            </tr>
          </tfoot>
        </table>
      </section>

      <section className="bp-section">
        <h3>Email</h3>
        <div className="cmp-field">
          <label>To</label>
          <input value={to} onChange={(e) => setTo(e.target.value)} placeholder="name@example.com" />
        </div>
        <div className="cmp-field">
          <label>Subject</label>
          <input
            value={subject}
            onChange={(e) => {
              setSubject(e.target.value);
              setEdited(true);
            }}
          />
        </div>

        {stale && (
          <div className="cmp-stale">
            You edited this email, and the prices or lines have changed since. Rebuild it to apply
            them (your edits will be replaced).{" "}
            <button className="link" onClick={regenerate}>
              Rebuild email
            </button>
          </div>
        )}

        <textarea
          className="cmp-body"
          value={body}
          onChange={(e) => {
            setBody(e.target.value);
            setEdited(true);
          }}
          rows={Math.min(30, Math.max(12, body.split("\n").length + 2))}
          spellCheck
        />

        <div className="cmp-actions">
          <button className="resolve" onClick={() => void copy(body, "Email")}>
            Copy email
          </button>
          <button className="link" onClick={() => void copy(subject, "Subject")}>
            Copy subject
          </button>
          <a className="link" href={mailto}>
            Open in Outlook
          </a>
          <button className="link" disabled={polishing} onClick={() => void polish()}>
            {polishing ? "Polishing…" : "Polish wording (AI)"}
          </button>
          {undo !== null && (
            <button
              className="link"
              onClick={() => {
                setBody(undo);
                setUndo(null);
              }}
            >
              Undo polish
            </button>
          )}
          {edited && (
            <button className="link" onClick={regenerate}>
              Reset to template
            </button>
          )}
        </div>
        {tooLong && (
          <p className="note">
            This email is long. Outlook can cut long emails short when opened from a link, so use
            Copy email and paste it into Outlook.
          </p>
        )}
        {notice && <p className="muted small cmp-notice">{notice}</p>}
      </section>
    </div>
  );
}
