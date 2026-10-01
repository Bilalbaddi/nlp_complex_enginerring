"use client";

import {
  Activity,
  BarChart3,
  Download,
  FileText,
  Gauge,
  Inbox,
  LayoutDashboard,
  LineChart,
  LogOut,
  Map as MapIcon,
  Menu,
  Moon,
  ShieldCheck,
  Sparkles,
  Sun,
  TrendingUp,
  Upload,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { useApi } from "@/lib/hooks";
import { navFor, ROLE_LABEL } from "@/lib/nav";
import { useTheme } from "@/lib/theme";
import type { HealthStatus } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Button } from "./ui";

const ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  layout: LayoutDashboard,
  inbox: Inbox,
  map: MapIcon,
  trending: TrendingUp,
  gauge: Gauge,
  line: LineChart,
  chart: BarChart3,
  sparkles: Sparkles,
  file: FileText,
  download: Download,
  upload: Upload,
  activity: Activity,
  shield: ShieldCheck,
};

export function AppShell({ children }: { children: React.ReactNode }): JSX.Element {
  const { user, loading, logout } = useAuth();
  const { theme, toggle } = useTheme();
  const router = useRouter();
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const health = useApi<HealthStatus>(user ? "/health" : null);

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [user, loading, router]);
  useEffect(() => {
    setOpen(false);
  }, [path]);

  if (loading || !user) return <p className="p-8 text-muted-foreground">Loading…</p>;
  const items = navFor(user.role);
  const current = items.find((n) => path.startsWith(n.href));
  // a role without access to this page is sent back to the overview instead of showing 403 errors
  if (!current && path !== "/dashboard") {
    router.replace("/dashboard");
    return <p className="p-8 text-muted-foreground">Redirecting…</p>;
  }

  const nav = (
    <nav aria-label="Main" className="flex flex-col gap-0.5 p-3">
      {items.map((n) => {
        const Icon = ICONS[n.icon] ?? LayoutDashboard;
        const active = path.startsWith(n.href);
        return (
          <Link
            key={n.href}
            href={n.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium",
              active ? "bg-primary text-primary-foreground" : "text-foreground hover:bg-muted",
            )}
          >
            <Icon className="h-4 w-4" />
            {n.label}
          </Link>
        );
      })}
    </nav>
  );

  return (
    <div className="min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:z-50 focus:bg-card focus:p-2">
        Skip to content
      </a>
      <aside className="no-print fixed inset-y-0 left-0 hidden w-60 overflow-y-auto border-r border-border bg-card lg:block">
        <div className="border-b border-border p-4">
          <p className="text-lg font-bold text-primary">SG-DSS</p>
          <p className="text-xs text-muted-foreground">Sahyadripur Municipal Corporation</p>
        </div>
        {nav}
      </aside>

      {open && (
        <div className="no-print fixed inset-0 z-40 lg:hidden">
          <button aria-label="Close menu" className="absolute inset-0 bg-black/40" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-64 overflow-y-auto bg-card">
            <div className="flex items-center justify-between border-b border-border p-4">
              <p className="text-lg font-bold text-primary">SG-DSS</p>
              <Button variant="ghost" size="icon" aria-label="Close menu" onClick={() => setOpen(false)}>
                <X className="h-4 w-4" />
              </Button>
            </div>
            {nav}
          </aside>
        </div>
      )}

      <div className="lg:pl-60">
        {health.data?.llm_demo_mode && (
          <div role="status" className="no-print bg-amber-100 px-4 py-2 text-sm text-amber-950">
            AI features are in demo mode (no Groq API key configured): AI text comes from a template provider, not a real model.
          </div>
        )}
        <header className="no-print sticky top-0 z-30 flex items-center justify-between gap-3 border-b border-border bg-card/95 px-4 py-2 backdrop-blur">
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="icon" className="lg:hidden" aria-label="Open menu" onClick={() => setOpen(true)}>
              <Menu className="h-5 w-5" />
            </Button>
            <span className="text-sm font-medium">{current?.label ?? "Overview"}</span>
          </div>
          <div className="flex items-center gap-2">
            <div className="hidden text-right text-xs sm:block">
              <p className="font-medium">{user.full_name}</p>
              <p className="text-muted-foreground">{ROLE_LABEL[user.role]}</p>
            </div>
            <Button variant="outline" size="icon" aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`} onClick={toggle}>
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </Button>
            <Button variant="outline" size="sm" onClick={() => void logout().then(() => router.replace("/login"))}>
              <LogOut className="h-4 w-4" />
              <span className="hidden sm:inline">Sign out</span>
            </Button>
          </div>
        </header>
        <main id="main" className="mx-auto max-w-7xl p-4 sm:p-6">
          {children}
        </main>
      </div>
    </div>
  );
}
