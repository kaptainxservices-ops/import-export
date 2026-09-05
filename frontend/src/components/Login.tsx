import { useState, type FormEvent } from "react";

import { supabase } from "../lib/supabase";

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
      </form>
    </div>
  );
}
