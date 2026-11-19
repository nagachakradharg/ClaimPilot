import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { Badge, Button, cn } from "./ui";

export const NOTICE = "Synthetic demo. Recommendations only; an adjuster makes final decisions.";

export function Notice() {
  return (
    <div className="bg-amber-100 px-4 py-1.5 text-center text-xs font-medium text-amber-900">
      {NOTICE}
    </div>
  );
}

export function Layout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  const links =
    user?.role === "claimant"
      ? [
          { to: "/", label: "My claims" },
          { to: "/submit", label: "New claim" },
        ]
      : [
          { to: "/", label: "Queue" },
          { to: "/dashboard", label: "Impact" },
        ];
  return (
    <div className="min-h-screen">
      <div className="sticky top-0 z-10">
        <Notice />
        <header className="border-b border-slate-200 bg-white/90 backdrop-blur">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
            <span className="font-semibold tracking-tight">
              Claim<span className="text-blue-600">Pilot</span>
            </span>
            <nav className="flex gap-1">
              {links.map((l) => (
                <NavLink
                  key={l.to}
                  to={l.to}
                  end
                  className={({ isActive }) =>
                    cn(
                      "rounded-md px-3 py-1.5 text-sm",
                      isActive
                        ? "bg-blue-50 font-medium text-blue-700"
                        : "text-slate-600 hover:text-slate-900",
                    )
                  }
                >
                  {l.label}
                </NavLink>
              ))}
            </nav>
            <div className="ml-auto flex items-center gap-3 text-sm">
              <span className="hidden text-slate-600 sm:inline">{user?.name}</span>
              <Badge tone="blue">{user?.role}</Badge>
              <Button variant="ghost" onClick={logout}>
                Sign out
              </Button>
            </div>
          </div>
        </header>
      </div>
      <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
    </div>
  );
}
