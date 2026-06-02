import { FC, FormEvent, useState } from "react";

interface Props {
  onLogin: (user: { email: string }) => void;
}

const LoginPage: FC<Props> = ({ onLogin }) => {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      // TODO: replace with the real /api/auth/login endpoint when provided.
      // For now: clicking Sign in always lets you in (no validation).
      onLogin({ email: email.trim() || "guest@inpharmd.local" });
    } catch (err: any) {
      setError(err?.message ?? "Login failed.");
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
          Welcome back. Sign in to manage manufacturer inquiries.
        </p>

        {error && <div className="error-banner">{error}</div>}

        <form className="auth-form" onSubmit={handleSubmit} noValidate>
          <label className="field">
            <span className="field-label">Email</span>
            <input
              type="email"
              autoComplete="email"
              placeholder="guest@inpharmd.ai"
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
