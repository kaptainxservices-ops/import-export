import { useEffect, useState } from "react";

import { supabase } from "../lib/supabase";
import type { Offer, SourceEmail } from "../lib/types";

/**
 * The original email behind a row.
 *
 * This is the trust anchor for the whole product. Every number on the board was read
 * out of a message by software, and the first time a price looks wrong the trader will
 * want to see what the supplier actually wrote. Without that, he stops believing the
 * board — and a board he does not believe is worse than no board, because he checks
 * everything twice.
 */
export function SourceDrawer({ offer, onClose }: { offer: Offer; onClose: () => void }) {
  const [email, setEmail] = useState<SourceEmail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    void (async () => {
      setLoading(true);
      const { data } = await supabase
        .from("emails")
        .select("id,subject,from_email,received_at,body_text,body_raw,needs_sender_review")
        .eq("id", offer.source_email_id)
        .single();
      setEmail((data as SourceEmail) ?? null);
      setLoading(false);
    })();
  }, [offer.source_email_id]);

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer" onClick={(e) => e.stopPropagation()}>
        <header>
          <div>
            <h2>{offer.description}</h2>
            <p className="muted">
              {offer.source_ref ?? "source unknown"}
              {offer.ean && ` · EAN ${offer.ean}`}
            </p>
          </div>
          <button className="close" onClick={onClose} aria-label="Close">
            ×
          </button>
        </header>

        <dl className="facts">
          <div>
            <dt>Quantity</dt>
            <dd>{offer.quantity ?? "—"}</dd>
          </div>
          <div>
            <dt>Price</dt>
            <dd>
              {offer.unit_price ?? "—"} {offer.currency ?? ""}
            </dd>
          </div>
          <div>
            <dt>Confirmed</dt>
            <dd>{new Date(offer.last_confirmed_at).toLocaleString()}</dd>
          </div>
          <div>
            <dt>First seen</dt>
            <dd>{new Date(offer.first_seen_at).toLocaleDateString()}</dd>
          </div>
        </dl>

        {loading && <p className="muted">Loading the original message…</p>}

        {!loading && !email && (
          <p className="error">The source email could not be loaded.</p>
        )}

        {email && (
          <>
            <h3>Original message</h3>
            <p className="muted">
              {email.from_email} · {new Date(email.received_at).toLocaleString()}
            </p>
            <p className="subject">{email.subject}</p>

            {email.needs_sender_review && (
              <p className="warn">
                The sender of this email could not be established automatically. It needs
                confirming before the offers on it are trusted.
              </p>
            )}

            {/* body_text is what was parsed — quoted history and signatures already
                stripped. Showing it rather than the raw message is the point: it is
                the evidence for what the extractor actually read. */}
            <pre className="body">{email.body_text || "(no plain text body)"}</pre>
          </>
        )}
      </aside>
    </div>
  );
}
