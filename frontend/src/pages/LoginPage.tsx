import { FC, FormEvent, useState } from "react";
import { api, session } from "../api";

export interface AuthUser {
  id: number;
  email: string;
  display_name?: string | null;
}

interface Props {
  onLogin: (user: AuthUser) => void;
}

const LoginPage: FC<Props> = ({ onLogin }) => {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!email.trim() || !password) {
      setError("Email and password are required.");
      return;
    }
    setSubmitting(true);
    try {
      const res = await api.auth.login(email.trim(), password);
      // Stash the session token so subsequent API calls auto-attach the header.
      session.set(res.session_token);
      onLogin({
        id: res.user.id,
        email: res.user.email,
        display_name: res.user.display_name,
      });
    } catch (err: any) {
      // Backend already maps upstream 401/422 to "Invalid email or password."
      const msg = err?.message ?? "Login failed.";
      // Strip the leading "401 Unauthorized: " prefix the generic request() adds.
      const clean = msg.replace(/^\d{3}\s+[^:]+:\s*/, "");
      try {
        const parsed = JSON.parse(clean);
        setError(parsed.detail ?? clean);
      } catch {
        setError(clean);
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <main className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <img src="/logo.png" alt="InpharmD" className="auth-logo" />
          <div className="auth-sub">Manufacturer MI Directory</div>
        </div>
        <h1 className="auth-title">Sign in</h1>
        <p className="auth-tagline">
          Sign in with your InpharmD credentials.
        </p>

        {error && <div className="error-banner">{error}</div>}

        <form className="auth-form" onSubmit={handleSubmit} noValidate>
          <label className="field">
            <span className="field-label">Email</span>
            <input
              type="email"
              autoComplete="email"
              placeholder="you@inpharmd.ai"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              disabled={submitting}
            />
          </label>
          <label className="field">
            <span className="field-label">Password</span>
            <input
              type="password"
              autoComplete="current-password"
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={submitting}
            />
          </label>
          <button
            type="submit"
            className="btn btn-primary auth-submit"
            disabled={submitting}
          >
            {submitting ? "Signing in…" : "Sign in"}
          </button>
        </form>

        <p className="auth-foot">
          Trouble signing in? Contact your admin.
        </p>
      </div>
    </main>
  );
};

export default LoginPage;
