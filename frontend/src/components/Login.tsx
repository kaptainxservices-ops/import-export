import { useState, type FormEvent } from "react";

import { supabase } from "../lib/supabase";

/**
 * The shared demo login, when one is configured.
 *
 * Read from the environment rather than written into this file, for one reason: a
 * password committed to a repository is public *forever*. It stays in the history long
 * after the demo account is gone, and it will be read by somebody who was never told
 * the account was temporary.
 *
 * Both variables must be present for the block to appear, so the client's own
 * deployment hides it by simply not setting them. Turning the demo off is a settings
 * change, not a code change and a redeploy of different code.
 */
const demoEmail = import.meta.env.VITE_DEMO_EMAIL;
const demoPassword = import.meta.env.VITE_DEMO_PASSWORD;

export function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);

    const { error: signInError } = await supabase.auth.signInWithPassword({
      email,
      password,
    });

    // Deliberately vague in production: a message distinguishing "no such user" from
    // "wrong password" tells an attacker which addresses are worth guessing at.
    //
    // In development it shows the real reason, because the two most common causes —
    // an unconfirmed email address and a disabled sign-in provider — are impossible to
    // tell apart from "those details were not accepted", and both look like a bug in
    // the dashboard rather than a setting in Supabase.
    if (signInError) {
      setError(
        import.meta.env.DEV
          ? `${signInError.message} (${signInError.status ?? "no status"})`
          : "Those details were not accepted.",
      );
    }
    setBusy(false);
  }

  // Typing a password off a screen is where a demo goes wrong — one mistyped character
  // and the reply is the deliberately unhelpful "those details were not accepted".
  function useDemoAccount() {
    setEmail(demoEmail ?? "");
    setPassword(demoPassword ?? "");
    setError(null);
  }

  return (
    <div className="centre">
      <form className="card login" onSubmit={submit}>
        <h1>Import-Export</h1>
        <p className="muted">Sign in to see the board.</p>

        <label>
          Email
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="username"
            required
          />
        </label>

        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </label>

        {error && <p className="error">{error}</p>}

        <button type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>

        {demoEmail && demoPassword && (
          <div className="demo">
            <p className="demo-title">Shared demo account</p>

            <dl className="demo-creds">
              <dt>Email</dt>
              <dd>{demoEmail}</dd>
              <dt>Password</dt>
              <dd>{demoPassword}</dd>
            </dl>

            <button type="button" className="link" onClick={useDemoAccount}>
              Fill these in
            </button>

            <p className="small muted">
              Everyone testing shares this login, so anything changed here is changed
              for everyone.
            </p>
          </div>
        )}
      </form>
    </div>
  );
}
