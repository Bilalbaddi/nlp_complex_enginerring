"use client";

import { X } from "lucide-react";
import { useState } from "react";
import { CategorySelect, WardSelect, useWards } from "@/components/Filters";
import { Async, Badge, Button, Card, Empty, ErrorBox, Field, Input, PageHeader, Pagination, Select, Table, toneFor } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { CATEGORIES, dateTime, label, num } from "@/lib/format";
import { qs, useApi, useDebounced } from "@/lib/hooks";
import type { Complaint, Page } from "@/lib/types";

function urgencyLevel(score: number | null): { text: string; tone: "bad" | "warn" | "info" | "neutral" } {
  if (score === null) return { text: "n/a", tone: "neutral" };
  if (score >= 70) return { text: "critical", tone: "bad" };
  if (score >= 50) return { text: "high", tone: "warn" };
  if (score >= 30) return { text: "medium", tone: "info" };
  return { text: "low", tone: "neutral" };
}

const STATUSES = ["open", "assigned", "in_progress", "resolved", "closed", "rejected"];
const REVIEWERS = ["ward_officer", "department_head", "analyst", "admin"];

function Detail({ c, onClose, onReviewed }: { c: Complaint; onClose: () => void; onReviewed: () => void }): JSX.Element {
  const { user } = useAuth();
  const [cat, setCat] = useState(c.predicted_category ?? c.category ?? CATEGORIES[0]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const urg = urgencyLevel(c.urgency_score);
  const canReview = c.needs_review && user && REVIEWERS.includes(user.role);

  async function review(): Promise<void> {
    setBusy(true);
    setErr(null);
    try {
      await api(`/nlp/review/${c.id}`, { method: "POST", body: JSON.stringify({ category: cat }) });
      onReviewed();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not save the review");
    } finally {
      setBusy(false);
    }
  }

  const row = (k: string, v: React.ReactNode): JSX.Element => (
    <div className="flex justify-between gap-3 border-b border-border/60 py-1.5 text-sm">
      <dt className="text-muted-foreground">{k}</dt>
      <dd className="text-right font-medium">{v}</dd>
    </div>
  );
  return (
    <aside role="dialog" aria-label={`Complaint ${c.id}`} className="fixed inset-y-0 right-0 z-40 w-full max-w-md overflow-y-auto border-l border-border bg-card p-5 shadow-xl">
      <div className="mb-3 flex items-start justify-between">
        <div>
          <h2 className="text-lg font-semibold">Complaint #{c.id}</h2>
          <p className="text-xs text-muted-foreground">{c.external_id}</p>
        </div>
        <Button variant="ghost" size="icon" aria-label="Close" onClick={onClose}>
          <X className="h-4 w-4" />
        </Button>
      </div>
      <p className="rounded-md bg-muted p-3 text-sm">{c.description}</p>
      <dl className="mt-3">
        {row("Status", <Badge tone={toneFor(c.status)}>{label(c.status)}</Badge>)}
        {row("Ward / department", `${c.ward_name ?? "-"} / ${c.department_name ?? "-"}`)}
        {row("Submitted", dateTime(c.submitted_at))}
        {row("Resolved", c.resolved_at ? `${dateTime(c.resolved_at)} (${num(c.resolution_hours, 1)} h)` : "Not yet")}
        {row("SLA", `${c.sla_hours ?? "-"} h ${c.sla_breached === true ? "· breached" : c.sla_breached === false ? "· met" : ""}`)}
        {row("Language / channel", `${c.language ?? "-"} / ${c.channel ?? "-"}`)}
        {row("Category (reported)", label(c.category))}
        {row("Category (AI predicted)", `${label(c.predicted_category)} ${c.category_confidence !== null ? `(${Math.round(c.category_confidence * 100)}%)` : ""}`)}
        {row("Urgency", <Badge tone={urg.tone}>{urg.text} · {num(c.urgency_score)}</Badge>)}
        {row("Sentiment", c.sentiment_score === null ? "-" : num(c.sentiment_score, 2))}
        {row("Citizen", `${c.citizen_name ?? "-"} · ${c.citizen_phone ?? "-"}`)}
        {c.duplicate_of_id && row("Possible duplicate of", `#${c.duplicate_of_id}`)}
        {c.entities && Object.entries(c.entities).filter(([, v]) => v.length).map(([k, v]) => row(`Detected ${k}`, v.join(", ")))}
      </dl>
      <p className="mt-2 text-xs text-muted-foreground">Names and phone numbers are masked except for roles that must contact citizens.</p>
      {c.needs_review && (
        <div className="mt-4 rounded-md border border-warning/50 p-3">
          <p className="text-sm font-medium text-warning">Needs human review</p>
          <p className="text-xs text-muted-foreground">{c.review_reason ?? "Low classifier confidence"}</p>
          {canReview && (
            <div className="mt-2 flex items-end gap-2">
              <Field label="Correct category" className="flex-1">
                <Select value={cat} onChange={(e) => setCat(e.target.value)}>
                  {CATEGORIES.map((x) => (
                    <option key={x} value={x}>
                      {label(x)}
                    </option>
                  ))}
                </Select>
              </Field>
              <Button onClick={() => void review()} disabled={busy}>
                Confirm
              </Button>
            </div>
          )}
          {err && <p role="alert" className="mt-2 text-sm text-destructive">{err}</p>}
        </div>
      )}
    </aside>
  );
}

export default function ComplaintsPage(): JSX.Element {
  const wards = useWards();
  const [f, setF] = useState({ q: "", category: "", ward_id: "", status: "", language: "", date_from: "", date_to: "", needs_review: "", sort: "newest" });
  const [page, setPage] = useState(1);
  const [sel, setSel] = useState<Complaint | null>(null);
  const q = useDebounced(f.q);
  const set = (k: keyof typeof f, v: string): void => {
    setF((p) => ({ ...p, [k]: v }));
    setPage(1);
  };
  const state = useApi<Page<Complaint>>(`/complaints${qs({ ...f, q, page, page_size: 20 })}`);

  return (
    <>
      <PageHeader title="Complaints explorer" subtitle="Search, filter and review citizen complaints with their AI analysis" />
      <Card className="mb-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Search text or ID">
            <Input value={f.q} onChange={(e) => set("q", e.target.value)} placeholder="e.g. pothole, GR-2024…" maxLength={100} />
          </Field>
          <CategorySelect value={f.category} onChange={(v) => set("category", v)} />
          <WardSelect value={f.ward_id} onChange={(v) => set("ward_id", v)} wards={wards} />
          <Field label="Status">
            <Select value={f.status} onChange={(e) => set("status", e.target.value)}>
              <option value="">Any status</option>
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {label(s)}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Language">
            <Select value={f.language} onChange={(e) => set("language", e.target.value)}>
              <option value="">Any language</option>
              <option value="en">English</option>
              <option value="hi">Hindi</option>
              <option value="mr">Marathi</option>
              <option value="hinglish">Hinglish</option>
            </Select>
          </Field>
          <Field label="From">
            <Input type="date" value={f.date_from} onChange={(e) => set("date_from", e.target.value)} />
          </Field>
          <Field label="To">
            <Input type="date" value={f.date_to} onChange={(e) => set("date_to", e.target.value)} />
          </Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Review">
              <Select value={f.needs_review} onChange={(e) => set("needs_review", e.target.value)}>
                <option value="">All</option>
                <option value="true">Needs review</option>
              </Select>
            </Field>
            <Field label="Sort">
              <Select value={f.sort} onChange={(e) => set("sort", e.target.value)}>
                <option value="newest">Newest</option>
                <option value="oldest">Oldest</option>
                <option value="urgency">Most urgent</option>
              </Select>
            </Field>
          </div>
        </div>
      </Card>

      <Card>
        {state.error && <ErrorBox message={state.error} onRetry={state.reload} />}
        <Async state={state} height="h-96">
          {(d) =>
            d.items.length === 0 ? (
              <Empty>No complaints match these filters.</Empty>
            ) : (
              <>
                <Table head={["ID", "Submitted", "Ward", "Category", "Urgency", "Status", "SLA", ""]}>
                  {d.items.map((c) => {
                    const u = urgencyLevel(c.urgency_score);
                    return (
                      <tr key={c.id} className="cursor-pointer hover:bg-muted/50" onClick={() => setSel(c)}>
                        <td className="tabular-nums">{c.id}</td>
                        <td className="whitespace-nowrap">{dateTime(c.submitted_at)}</td>
                        <td>{c.ward_name}</td>
                        <td>
                          {label(c.category)}
                          {c.predicted_category && c.predicted_category !== c.category && <span className="ml-1 text-xs text-muted-foreground">(AI: {label(c.predicted_category)})</span>}
                        </td>
                        <td><Badge tone={u.tone}>{u.text}</Badge></td>
                        <td><Badge tone={toneFor(c.status)}>{label(c.status)}</Badge></td>
                        <td>{c.sla_breached === true ? <Badge tone="bad">breached</Badge> : c.sla_breached === false ? <Badge tone="good">met</Badge> : "-"}</td>
                        <td>
                          <Button size="sm" variant="ghost" onClick={() => setSel(c)}>
                            {c.needs_review ? "Review" : "Details"}
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </Table>
                <Pagination page={d.meta.page} pages={d.meta.pages} total={d.meta.total} onPage={setPage} />
              </>
            )
          }
        </Async>
      </Card>
      {sel && (
        <Detail
          c={sel}
          onClose={() => setSel(null)}
          onReviewed={() => {
            setSel(null);
            state.reload();
          }}
        />
      )}
    </>
  );
}
