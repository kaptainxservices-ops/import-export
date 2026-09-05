import { createClient } from "@supabase/supabase-js";

/**
 * The browser client, using the PUBLISHABLE key.
 *
 * This key is safe here for exactly one reason: row-level security is enabled on every
 * table, so what a signed-in user can read is decided by Postgres rather than by this
 * code. The secret key must never appear in this file — Vite inlines anything prefixed
 * VITE_ into the bundle, where it is readable by anyone who opens the page, and the
 * secret key bypasses every policy.
 */
const url = import.meta.env.VITE_SUPABASE_URL;
const key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY;

if (!url || !key) {
  throw new Error(
    "VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY must be set. " +
      "Copy frontend/.env.example to frontend/.env.local and fill them in.",
  );
}

if (key.startsWith("sb_secret_") || key.startsWith("eyJ")) {
  throw new Error(
    "That looks like the SECRET key. The dashboard must use the publishable key — " +
      "the secret one bypasses row-level security and would be visible to every visitor.",
  );
}

export const supabase = createClient(url, key, {
  auth: { persistSession: true, autoRefreshToken: true },
});
