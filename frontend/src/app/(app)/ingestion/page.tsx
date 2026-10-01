"use client";

import { useState } from "react";
import { WardSelect, useWards } from "@/components/Filters";
import { Async, Badge, Button, Card, CardHeader, ErrorBox, Field, Input, PageHeader, Pagination, Select, Spinner, Table, toneFor } from "@/components/ui";
import { ApiError, API_URL, api, tokens } from "@/lib/api";
import { CATEGORIES, dateTime, label } from "@/lib/format";
import { qs, useApi } from "@/lib/hooks";
import type { Complaint, IngestionLog, Page } from "@/lib/types";

const KINDS = [
  { id: "complaints", name: "Complaints", hint: "Columns: description, submitted_at, ward, category/department, status, latitude/longitude, … Duplicates and invalid rows are reported, not loaded." },
  { id: "feedback", name: "Citizen feedback", hint: "Columns: rating (1-5), text, ward, department, submitted_at." },
  { id: "performance", name: "Service performance (SLA)", hint: "Monthly per department/ward: complaints received/resolved, SLA breaches, avg resolution hours, staff." },
];

function SingleComplaint({ onCreated }: { onCreated: () => void }): JSX.Element {
  const wards = useWards();
  const [text, setText] = useState("");
  const [ward, setWard] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<{ ok: boolean; text: string } | null>(null);

  async function submit(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    setBusy(true);
    setRes(null);
    try {
      const wardName = wards.find((w) => String(w.id) === ward)?.name;
      const c = await api<Complaint>("/complaints", { method: "POST", body: JSON.stringify({ description: text, ward: wardName, channel: "web" }) });
      setRes({ ok: true, text: `Registered complaint #${c.id}. It is classified and scored in the background (see the live feed on the overview).` });
      setText("");
      onCreated();
    } catch (err) {
      setRes({ ok: false, text: err instanceof ApiError ? err.message : "Could not register the complaint" });
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card>
      <CardHeader title="Register one complaint" subtitle="Simulates the real-time feed: validated, deduplicated, then classified by the NLP engine" />
      <form onSubmit={(e) => void submit(e)} className="space-y-3">
        <Field label="Description">
          <textarea className="min-h-20 w-full rounded-md border border-border bg-card p-2 text-sm" value={text} onChange={(e) => setText(e.target.value)} minLength={5} maxLength={5000} required placeholder="e.g. Huge pothole on Ring Road near the bus stand, two bikes fell yesterday" />
        </Field>
        <div className="grid items-end gap-3 sm:grid-cols-2">
          <WardSelect value={ward} onChange={setWard} wards={wards} all="Detect from text / location" />
          <Button type="submit" disabled={busy || text.length < 5}>
            {busy && <Spinner />}Submit complaint
          </Button>
        </div>
      </form>
      {res && (res.ok ? <p role="status" className="mt-3 text-sm text-success">{res.text}</p> : <div className="mt-3"><ErrorBox message={res.text} /></div>)}
    </Card>
  );
}

export default function IngestionPage(): JSX.Element {
  const [kind, setKind] = useState("complaints");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<number | null>(null);
  const logs = useApi<Page<IngestionLog>>(`/ingestion-logs${qs({ page, page_size: 15 })}`, { pollMs: 5000 });
  void CATEGORIES;

  async function upload(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setMsg(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(`${API_URL}/ingest/${kind}`, { method: "POST", headers: { Authorization: `Bearer ${tokens.access ?? ""}` }, body: fd });
      const body = (await res.json()) as { success: boolean; error: { message: string } | null };
      if (!res.ok || !body.success) throw new ApiError(res.status, "upload", body.error?.message ?? "Upload failed");
      setMsg({ ok: true, text: "File accepted and queued. Progress appears in the log below." });
      setFile(null);
      logs.reload();
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Upload failed" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title="Data ingestion" subtitle="Import complaints, feedback and service performance files (CSV, Excel, JSON) with validation, normalisation and deduplication" />
      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Upload a file" subtitle="Up to 25 MB. Re-uploading the same file is safe: duplicates are skipped." />
          <form onSubmit={(e) => void upload(e)} className="space-y-3">
            <Field label="Data type">
              <Select value={kind} onChange={(e) => setKind(e.target.value)}>
                {KINDS.map((k) => (
                  <option key={k.id} value={k.id}>
                    {k.name}
                  </option>
                ))}
              </Select>
            </Field>
            <p className="text-xs text-muted-foreground">{KINDS.find((k) => k.id === kind)?.hint}</p>
            <Field label="File (.csv, .xlsx, .json, .jsonl)">
              <Input type="file" accept=".csv,.xlsx,.xls,.json,.jsonl" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="h-auto py-1.5" />
            </Field>
            <Button type="submit" disabled={busy || !file}>
              {busy && <Spinner />}Upload and import
            </Button>
          </form>
          {msg && (msg.ok ? <p role="status" className="mt-3 text-sm text-success">{msg.text}</p> : <div className="mt-3"><ErrorBox message={msg.text} /></div>)}
        </Card>
        <SingleComplaint onCreated={() => undefined} />
      </div>

      <Card className="mt-4">
        <CardHeader title="Ingestion audit log" subtitle="Every import, with accepted, rejected and duplicate row counts (refreshes every 5 s)" />
        <Async state={logs} height="h-48">
          {(d) => (
            <>
              <Table head={["#", "Started", "Type", "File", "Status", "Rows", "Accepted", "Rejected", "Duplicates", ""]}>
                {d.items.map((l) => (
                  <tr key={l.id}>
                    <td>{l.id}</td>
                    <td className="whitespace-nowrap">{dateTime(l.started_at)}</td>
                    <td>{label(l.source_type)}</td>
                    <td className="max-w-48 truncate" title={l.filename ?? ""}>{l.filename}</td>
                    <td><Badge tone={toneFor(l.status)}>{l.status}</Badge></td>
                    <td className="tabular-nums">{l.rows_total}</td>
                    <td className="tabular-nums">{l.rows_ok}</td>
                    <td className="tabular-nums">{l.rows_rejected}</td>
                    <td className="tabular-nums">{l.rows_duplicate}</td>
                    <td>
                      {l.errors && (
                        <Button size="sm" variant="ghost" onClick={() => setOpen(open === l.id ? null : l.id)}>
                          {open === l.id ? "Hide" : "Details"}
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </Table>
              {open !== null && <pre className="mt-3 max-h-64 overflow-auto rounded-md bg-muted p-3 text-xs">{JSON.stringify(d.items.find((l) => l.id === open)?.errors, null, 2)}</pre>}
              <Pagination page={d.meta.page} pages={d.meta.pages} total={d.meta.total} onPage={setPage} />
            </>
          )}
        </Async>
      </Card>
    </>
  );
}
