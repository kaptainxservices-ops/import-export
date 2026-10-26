import { useEffect, useMemo, useState } from "react";

import { supabase } from "../lib/supabase";

interface StoredAttachment {
  filename: string;
  kind?: string;
  rows?: string[][] | null;
  text?: string | null;
}

interface StoredEmail {
  id: string;
  subject: string | null;
  from_email: string;
  received_at: string;
  body_text: string | null;
  body_raw: string | null;
  attachments: StoredAttachment[] | null;
}

// A 2,000-row sheet in the DOM makes the drawer sluggish for nothing: the decision is
// made from the top of the list and the shape of it, not from row 1,800.
const ROW_LIMIT = 400;

/**
 * The email behind a review decision, as it arrived.
 *
 * Approving or rejecting a list means trusting what the parser read, and the only way
 * to check that is to see what the supplier sent: the message, and every sheet of every
 * attachment as rows. Read-only — nothing here changes the board.
 */
export function EmailDrawer({
  emailId,
  title,
  onClose,
}: {
  emailId: string;
  title?: string | null;
  onClose: () => void;
}) {
  const [email, setEmail] = useState<StoredEmail | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<number>(-1); // -1 is the message; 0.. are attachments
  const [formatted, setFormatted] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      setLoading(true);
      const { data } = await supabase
        .from("emails")
        .select("id,subject,from_email,received_at,body_text,body_raw,attachments")
        .eq("id", emailId)
        .single();
      if (cancelled) return;
      const loaded = (data as StoredEmail) ?? null;
      setEmail(loaded);
      // A price list is in its attachment; open there rather than on a two-line note.
      const sheets = (loaded?.attachments ?? []).filter((a) => a.rows && a.rows.length);
      setTab(sheets.length > 0 && (loaded?.body_text ?? "").trim().length < 400 ? 0 : -1);
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [emailId]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const sheets = useMemo(
    () => (email?.attachments ?? []).filter((a) => (a.rows && a.rows.length) || a.text),
    [email],
  );
  const others = (email?.attachments ?? []).length - sheets.length;
  const hasHtml = /<(table|html|div|p)\b/i.test(email?.body_raw ?? "");

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside
        className="drawer wide"
        role="dialog"
        aria-label="Original email"
        onClick={(e) => e.stopPropagation()}
      >
        <header>
          <div>
            <h2>{email?.subject || title || "Original email"}</h2>
            {email && (
              <p className="muted">
                {email.from_email} · {new Date(email.received_at).toLocaleString()}
              </p>
            )}
          </div>
          <button className="close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        {loading && <p className="muted">Loading the email…</p>}
        {!loading && !email && <p className="error">This email could not be loaded.</p>}

        {email && (
          <>
            <div className="email-tabs" role="tablist">
              <button
                role="tab"
                aria-selected={tab === -1}
                className={tab === -1 ? "email-tab on" : "email-tab"}
                onClick={() => setTab(-1)}
              >
                Message
              </button>
              {sheets.map((sheet, i) => (
                <button
                  key={i}
                  role="tab"
                  aria-selected={tab === i}
                  className={tab === i ? "email-tab on" : "email-tab"}
                  onClick={() => setTab(i)}
                  title={sheet.filename}
                >
                  {sheetLabel(sheet.filename)}
                  {sheet.rows && <span className="email-tab-count">{sheet.rows.length}</span>}
                </button>
              ))}
            </div>

            {others > 0 && (
              <p className="muted small">
                {others} more attachment{others === 1 ? "" : "s"} (images or files that could
                not be read as rows) not shown.
              </p>
            )}

            {tab === -1 ? (
              <>
                {hasHtml && (
                  <div className="pills">
                    <button className={formatted ? "pill" : "pill on"} onClick={() => setFormatted(false)}>
                      As the system read it
                    </button>
                    <button className={formatted ? "pill on" : "pill"} onClick={() => setFormatted(true)}>
                      As it was sent
                    </button>
                  </div>
                )}
                {formatted && hasHtml ? (
                  // Sandboxed with no permissions: no scripts, no forms, no navigation.
                  // A supplier's HTML is shown, never run.
                  <iframe
                    className="email-html"
                    sandbox=""
                    title="Email as sent"
                    srcDoc={email.body_raw ?? ""}
                  />
                ) : (
                  <pre className="body">{email.body_text || "(no plain text body)"}</pre>
                )}
              </>
            ) : (
              <SheetView sheet={sheets[tab]} />
            )}
          </>
        )}
      </aside>
    </div>
  );
}

function sheetLabel(filename: string): string {
  // 'WH_BRAND_NEW_STOCK_26.10.26.xlsx!BRAND NEW COLOUR-WISE STOCK' -> the sheet name,
  // which is what tells two tabs of one workbook apart.
  const [file, sheet] = filename.split("!");
  return (sheet || file).trim() || filename;
}

/** Excel's float noise, shown as the number the supplier typed: 380.79999999999995 -> 380.8. */
function tidy(cell: string): string {
  if (/^-?\d+\.\d{6,}$/.test(cell)) {
    return String(Math.round(Number(cell) * 1e6) / 1e6);
  }
  return cell;
}

function SheetView({ sheet }: { sheet: StoredAttachment }) {
  if (!sheet.rows || sheet.rows.length === 0) {
    return <pre className="body">{sheet.text || "(empty)"}</pre>;
  }

  const rows = sheet.rows.slice(0, ROW_LIMIT);
  const width = Math.max(...rows.map((r) => r.length));

  return (
    <>
      <p className="muted small">
        {sheet.filename.split("!")[0]}
        {sheet.rows.length > ROW_LIMIT &&
          ` · showing the first ${ROW_LIMIT} of ${sheet.rows.length} rows`}
      </p>
      <div className="sheet-scroll">
        <table className="sheet">
          <tbody>
            {rows.map((row, r) => (
              <tr key={r}>
                <th scope="row">{r + 1}</th>
                {Array.from({ length: width }, (_, c) => (
                  <td key={c}>{tidy(row[c] ?? "")}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
