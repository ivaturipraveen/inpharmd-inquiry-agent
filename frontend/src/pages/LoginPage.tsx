import { FC, FormEvent, useEffect, useState } from "react";
import { api, session } from "../api";

export interface AuthUser {
  id: number;
  email: string;
  display_name?: string | null;
}

interface Props {
  onLogin: (user: AuthUser) => void;
}

interface OtpState {
  emailToken: string;
  message: string;
  email: string;
}

function parseApiError(err: any): string {
  const msg = err?.message ?? "Request failed.";
  const clean = msg.replace(/^\d{3}\s+[^:]+:\s*/, "");
  try {
    const parsed = JSON.parse(clean);
    return parsed.detail ?? clean;
  } catch {
    return clean;
  }
}

const LoginPage: FC<Props> = ({ onLogin }) => {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // OTP step
  const [otpState, setOtpState] = useState<OtpState | null>(null);
  const [otp, setOtp] = useState("");
  const [resending, setResending] = useState(false);
  const [resendMessage, setResendMessage] = useState<string | null>(null);

  useEffect(() => {
    if (!error) return;
    const t = setTimeout(() => setError(null), 6000);
    return () => clearTimeout(t);
  }, [error]);

  useEffect(() => {
    if (!resendMessage) return;
    const t = setTimeout(() => setResendMessage(null), 6000);
    return () => clearTimeout(t);
  }, [resendMessage]);

  const handleLogin = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!email.trim() || !password) {
      setError("Email and password are required.");
      return;
    }
    setSubmitting(true);
    try {
      const res = await api.auth.login(email.trim(), password);
      if ("session_token" in res) {
        session.set(res.session_token);
        onLogin({ id: res.user.id, email: res.user.email, display_name: res.user.display_name });
      } else {
        setOtpState({ emailToken: res.email_token, message: res.message, email: res.email });
        setOtp("");
        setResendMessage(null);
      }
    } catch (err: any) {
      setError(parseApiError(err));
    } finally {
      setSubmitting(false);
    }
  };

  const handleVerifyOtp = async (e: FormEvent) => {
    e.preventDefault();
    if (!otpState) return;
    setError(null);
    setResendMessage(null);
    if (!otp.trim()) {
      setError("Please enter the verification code.");
      return;
    }
    setSubmitting(true);
    try {
      const res = await api.auth.verifyOtp(otpState.emailToken, otp.trim());
      session.set(res.session_token);
      onLogin({ id: res.user.id, email: res.user.email, display_name: res.user.display_name });
    } catch {
      setError("Invalid verification code. Please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleResend = async () => {
    if (!otpState || resending) return;
    setResendMessage(null);
    setError(null);
    setResending(true);
    try {
      const res = await api.auth.resendOtp(otpState.emailToken);
      setOtpState((prev) => prev ? { ...prev, emailToken: res.email_token || prev.emailToken, message: res.message || prev.message } : prev);
      setResendMessage("A new code has been sent.");
    } catch (err: any) {
      setError(parseApiError(err));
    } finally {
      setResending(false);
    }
  };

  const brand = (
    <div className="auth-brand">
      <img src="/logo.png" alt="InpharmD" className="auth-logo" />
      <div className="auth-sub">Manufacturer MI Directory</div>
    </div>
  );

  // ── OTP screen ──────────────────────────────────────────────────────────────
  if (otpState) {
    return (
      <main className="auth-page">
        <div className="auth-card">
          {brand}
          <h1 className="auth-title">Verify your identity</h1>
          <p className="auth-tagline">{otpState.message || `A verification code was sent to ${otpState.email}.`}</p>

          {error && <div className="error-banner">{error}</div>}
          {resendMessage && <div className="success-banner">{resendMessage}</div>}

          <form className="auth-form" onSubmit={handleVerifyOtp} noValidate>
            <label className="field">
              <span className="field-label">Verification code</span>
              <input
                type="text"
                inputMode="numeric"
                autoComplete="one-time-code"
                placeholder="Enter code"
                value={otp}
                onChange={(e) => setOtp(e.target.value)}
                disabled={submitting}
                autoFocus
              />
            </label>
            <button
              type="submit"
              className="btn btn-primary auth-submit"
              disabled={submitting || !otp.trim()}
            >
              {submitting ? "Verifying…" : "Verify"}
            </button>
          </form>

          <div className="auth-foot" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <button
              type="button"
              className="btn-link"
              onClick={() => { setOtpState(null); setError(null); setResendMessage(null); }}
              disabled={submitting}
            >
              ← Back
            </button>
            <button
              type="button"
              className="btn-link"
              onClick={handleResend}
              disabled={resending || submitting}
            >
              {resending ? "Sending…" : "Resend code"}
            </button>
          </div>
        </div>
      </main>
    );
  }

  // ── Sign-in screen ───────────────────────────────────────────────────────────
  return (
    <main className="auth-page">
      <div className="auth-card">
        {brand}
        <h1 className="auth-title">Sign in</h1>
        <p className="auth-tagline">Sign in with your InpharmD credentials.</p>

        {error && <div className="error-banner">{error}</div>}

        <form className="auth-form" onSubmit={handleLogin} noValidate>
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

        <p className="auth-foot">Trouble signing in? Contact your admin.</p>
      </div>
    </main>
  );
};

export default LoginPage;
