import { useState, type FormEvent } from "react";
import { Notice } from "../components/Layout";
import { Button, Card, ErrorNote, Field, Input } from "../components/ui";
import { useAuth } from "../lib/authContext";

// Seeded demo accounts (synthetic people). The shared demo password is in the README.
const DEMO = [
  { email: "casey.fofana@example.com", label: "Claimant (Casey)" },
  { email: "adjuster@claimpilot.example", label: "Adjuster" },
  { email: "auditor@claimpilot.example", label: "Auditor" },
];

export default function Login() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen">
      <Notice />
      <div className="mx-auto mt-16 max-w-sm px-4">
        <h1 className="mb-1 text-2xl font-semibold tracking-tight">
          Claim<span className="text-blue-600">Pilot</span>
        </h1>
        <p className="mb-6 text-sm text-slate-600">
          Auto-claims triage with a human review gate.
        </p>
        <Card>
          <form onSubmit={onSubmit} className="space-y-4">
            <Field label="Email">
              <Input
                type="email"
                autoComplete="username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </Field>
            <Field label="Password">
              <Input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </Field>
            <ErrorNote error={error} />
            <Button type="submit" className="w-full" disabled={busy}>
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </Card>
        <div className="mt-6">
          <p className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">Demo accounts</p>
          <div className="flex flex-wrap gap-2">
            {DEMO.map((d) => (
              <Button key={d.email} variant="secondary" type="button" onClick={() => setEmail(d.email)}>
                {d.label}
              </Button>
            ))}
          </div>
          <p className="mt-2 text-xs text-slate-500">The demo password is listed in the README.</p>
        </div>
      </div>
    </div>
  );
}
