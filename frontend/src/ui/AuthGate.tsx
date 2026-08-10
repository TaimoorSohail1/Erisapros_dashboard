import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { ShieldCheck } from "lucide-react";
import {
  authenticationEnabled,
  completeNewPassword,
  getIdToken,
  signIn,
} from "../auth";
import { BrandedLoader, InlineLoader } from "./Loading";

export function AuthGate({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(!authenticationEnabled());
  const [authenticated, setAuthenticated] = useState(!authenticationEnabled());
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [requiresNewPassword, setRequiresNewPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!authenticationEnabled()) return;
    getIdToken()
      .then((token) => setAuthenticated(Boolean(token)))
      .catch(() => setAuthenticated(false))
      .finally(() => setReady(true));
  }, []);

  async function submitLogin(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      const result = await signIn(email, password);
      if (result === "new-password-required") {
        setRequiresNewPassword(true);
      } else {
        setAuthenticated(true);
      }
    } catch (loginError) {
      setError(loginError instanceof Error ? loginError.message : "Login failed.");
    } finally {
      setSubmitting(false);
    }
  }

  async function submitNewPassword(event: FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      await completeNewPassword(newPassword);
      setAuthenticated(true);
    } catch (passwordError) {
      setError(passwordError instanceof Error ? passwordError.message : "Password update failed.");
    } finally {
      setSubmitting(false);
    }
  }

  if (!ready) {
    return <main className="auth-loading"><BrandedLoader label="Preparing your workspace" detail="Checking your secure session…" /></main>;
  }
  if (authenticated) return <>{children}</>;

  return (
    <main className="auth-page">
      <section className="auth-card">
        <div className="auth-mark"><ShieldCheck size={28} /></div>
        <p className="auth-kicker">ERISAPros</p>
        <h1>{requiresNewPassword ? "Create your password" : "Sign in"}</h1>
        <p className="subtle">
          {requiresNewPassword
            ? "Replace the temporary password sent to your email."
            : "Access the Schedule A and Form 5500 review workspace."}
        </p>

        <form onSubmit={requiresNewPassword ? submitNewPassword : submitLogin} className="auth-form">
          {!requiresNewPassword && (
            <>
              <label>
                <span>Email</span>
                <input type="email" value={email} onChange={(event) => setEmail(event.target.value)} required autoComplete="username" />
              </label>
              <label>
                <span>Password</span>
                <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} required autoComplete="current-password" />
              </label>
            </>
          )}
          {requiresNewPassword && (
            <label>
              <span>New password</span>
              <input type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} minLength={12} required autoComplete="new-password" />
            </label>
          )}
          {error && <div className="auth-error" role="alert">{error}</div>}
          <button className="button" type="submit" disabled={submitting}>
            {submitting ? <InlineLoader label={requiresNewPassword ? "Setting password" : "Signing in"} /> : requiresNewPassword ? "Set password" : "Sign in"}
          </button>
        </form>
      </section>
    </main>
  );
}
