"use client";

import { useState } from "react";
import { WardSelect, useDepartments, useWards } from "@/components/Filters";
import { Async, Badge, Button, Card, CardHeader, ErrorBox, Field, Input, PageHeader, Pagination, Select, Table, Tabs, toneFor } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { dateTime, label, num } from "@/lib/format";
import { qs, useApi } from "@/lib/hooks";
import { ROLE_LABEL } from "@/lib/nav";
import type { AuditLog, Job, Page, Role, User } from "@/lib/types";

const ROLES = Object.keys(ROLE_LABEL) as Role[];

function UsersTab(): JSX.Element {
  const wards = useWards();
  const depts = useDepartments();
  const [page, setPage] = useState(1);
  const [q, setQ] = useState("");
  const users = useApi<Page<User>>(`/users${qs({ page, page_size: 15, q })}`);
  const [form, setForm] = useState({ email: "", full_name: "", password: "", role: "analyst" as Role, ward_id: "", department_id: "" });
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function create(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    setMsg(null);
    try {
      await api("/users", {
        method: "POST",
        body: JSON.stringify({ ...form, ward_id: form.ward_id ? Number(form.ward_id) : null, department_id: form.department_id ? Number(form.department_id) : null }),
      });
      setMsg({ ok: true, text: `Created ${form.email}` });
      setForm({ ...form, email: "", full_name: "", password: "" });
      users.reload();
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError ? err.message : "Could not create the user" });
    }
  }
  async function patch(id: number, body: Record<string, unknown>): Promise<void> {
    try {
      await api(`/users/${id}`, { method: "PATCH", body: JSON.stringify(body) });
      users.reload();
    } catch (err) {
      setMsg({ ok: false, text: err instanceof ApiError ? err.message : "Update failed" });
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Add a user" subtitle="Password: 8+ characters with letters and digits. Ward officers need a ward; department heads a department." />
        <form onSubmit={(e) => void create(e)} className="grid gap-3 sm:grid-cols-3">
          <Field label="Email"><Input type="email" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
          <Field label="Full name"><Input required minLength={2} value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></Field>
          <Field label="Password"><Input type="password" required minLength={8} autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></Field>
          <Field label="Role">
            <Select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}>
              {ROLES.map((r) => (
                <option key={r} value={r}>
                  {ROLE_LABEL[r]}
                </option>
              ))}
            </Select>
          </Field>
          <WardSelect value={form.ward_id} onChange={(v) => setForm({ ...form, ward_id: v })} wards={wards} all="No ward" />
          <Field label="Department">
            <Select value={form.department_id} onChange={(e) => setForm({ ...form, department_id: e.target.value })}>
              <option value="">No department</option>
              {depts.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </Select>
          </Field>
          <div><Button type="submit">Create user</Button></div>
        </form>
        {msg && (msg.ok ? <p role="status" className="mt-3 text-sm text-success">{msg.text}</p> : <div className="mt-3"><ErrorBox message={msg.text} /></div>)}
      </Card>
      <Card>
        <div className="mb-3 max-w-xs"><Input aria-label="Search users" placeholder="Search by name or email" value={q} onChange={(e) => { setQ(e.target.value); setPage(1); }} /></div>
        <Async state={users}>
          {(d) => (
            <>
              <Table head={["Name", "Email", "Role", "Ward / dept", "Last login", "Status", ""]}>
                {d.items.map((u) => (
                  <tr key={u.id}>
                    <td className="font-medium">{u.full_name}</td>
                    <td>{u.email}</td>
                    <td>
                      <Select aria-label={`Role of ${u.email}`} className="h-8 w-40" value={u.role} onChange={(e) => void patch(u.id, { role: e.target.value })}>
                        {ROLES.map((r) => (
                          <option key={r} value={r}>
                            {ROLE_LABEL[r]}
                          </option>
                        ))}
                      </Select>
                    </td>
                    <td className="text-muted-foreground">{wards.find((w) => w.id === u.ward_id)?.name ?? "-"} / {depts.find((x) => x.id === u.department_id)?.code ?? "-"}</td>
                    <td>{dateTime(u.last_login_at)}</td>
                    <td><Badge tone={u.is_active ? "good" : "bad"}>{u.is_active ? "active" : "disabled"}</Badge></td>
                    <td><Button size="sm" variant="outline" onClick={() => void patch(u.id, { is_active: !u.is_active })}>{u.is_active ? "Disable" : "Enable"}</Button></td>
                  </tr>
                ))}
              </Table>
              <Pagination page={d.meta.page} pages={d.meta.pages} total={d.meta.total} onPage={setPage} />
            </>
          )}
        </Async>
      </Card>
    </div>
  );
}

function AuditTab(): JSX.Element {
  const [page, setPage] = useState(1);
  const [action, setAction] = useState("");
  const logs = useApi<Page<AuditLog>>(`/audit-logs${qs({ page, page_size: 25, action })}`);
  return (
    <Card>
      <div className="mb-3 max-w-xs"><Input aria-label="Filter by action" placeholder="Filter by action, e.g. login" value={action} onChange={(e) => { setAction(e.target.value); setPage(1); }} /></div>
      <Async state={logs}>
        {(d) => (
          <>
            <Table head={["When", "User", "Action", "Resource", "IP"]}>
              {d.items.map((l) => (
                <tr key={l.id}>
                  <td className="whitespace-nowrap">{dateTime(l.created_at)}</td>
                  <td>{l.user_email ?? "-"}</td>
                  <td><Badge>{l.action}</Badge></td>
                  <td className="text-muted-foreground">{l.resource_type ? `${l.resource_type} #${l.resource_id ?? ""}` : "-"}</td>
                  <td className="text-muted-foreground">{l.ip_address ?? "-"}</td>
                </tr>
              ))}
            </Table>
            <Pagination page={d.meta.page} pages={d.meta.pages} total={d.meta.total} onPage={setPage} />
          </>
        )}
      </Async>
    </Card>
  );
}

function JobsTab(): JSX.Element {
  const [page, setPage] = useState(1);
  const jobs = useApi<Page<Job>>(`/jobs${qs({ page, page_size: 15 })}`, { pollMs: 8000 });
  const [msg, setMsg] = useState<string | null>(null);
  const RUNNABLE = [
    { type: "brief.generate", label: "Regenerate policy brief", payload: { refresh: true } },
    { type: "documents.embed", label: "Embed new document chunks", payload: {} },
    { type: "embeddings.backfill", label: "Embed complaints missing vectors", payload: { max_seconds: 300 } },
    { type: "nlp.predict_all", label: "Re-run all NLP predictions", payload: {} },
  ];
  async function run(type: string, payload: Record<string, unknown>): Promise<void> {
    try {
      const j = await api<Job>("/jobs", { method: "POST", body: JSON.stringify({ type, payload }) });
      setMsg(`Queued job #${j.id} (${type})`);
      jobs.reload();
    } catch (err) {
      setMsg(err instanceof ApiError ? err.message : "Could not queue the job");
    }
  }
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="Background jobs" subtitle="Run maintenance tasks. They execute in the API process (or the worker, if running)." />
        <div className="flex flex-wrap gap-2">
          {RUNNABLE.map((r) => (
            <Button key={r.type} variant="outline" onClick={() => void run(r.type, r.payload)}>
              {r.label}
            </Button>
          ))}
        </div>
        {msg && <p role="status" className="mt-2 text-sm text-muted-foreground">{msg}</p>}
      </Card>
      <Card>
        <Async state={jobs}>
          {(d) => (
            <>
              <Table head={["#", "Type", "Status", "Attempts", "Created", "Finished", "Error"]}>
                {d.items.map((j) => (
                  <tr key={j.id}>
                    <td>{j.id}</td>
                    <td>{j.type}</td>
                    <td><Badge tone={toneFor(j.status)}>{j.status}</Badge></td>
                    <td className="tabular-nums">{j.attempts}/{j.max_attempts}</td>
                    <td className="whitespace-nowrap">{dateTime(j.created_at)}</td>
                    <td className="whitespace-nowrap">{dateTime(j.finished_at)}</td>
                    <td className="max-w-xs truncate text-destructive" title={j.error ?? ""}>{j.error}</td>
                  </tr>
                ))}
              </Table>
              <Pagination page={d.meta.page} pages={d.meta.pages} total={d.meta.total} onPage={setPage} />
            </>
          )}
        </Async>
      </Card>
    </div>
  );
}

function MasterTab(): JSX.Element {
  const wards = useWards();
  const depts = useDepartments();
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card>
        <CardHeader title="Wards" subtitle="Master data loaded from the ward boundary file" />
        <Table head={["Code", "Name", "Population", "Area km²", "Road km"]}>
          {wards.map((w) => (
            <tr key={w.id}>
              <td>{w.code}</td>
              <td>{w.name}</td>
              <td className="tabular-nums">{num(w.population)}</td>
              <td className="tabular-nums">{num(w.area_sq_km, 1)}</td>
              <td className="tabular-nums">{num(w.road_km, 0)}</td>
            </tr>
          ))}
        </Table>
      </Card>
      <Card>
        <CardHeader title="Departments" />
        <Table head={["Code", "Name", "Serves category", "SLA (h)"]}>
          {depts.map((d) => (
            <tr key={d.id}>
              <td>{d.code}</td>
              <td>{d.name}</td>
              <td>{label(d.category)}</td>
              <td className="tabular-nums">{d.sla_hours}</td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}

export default function AdminPage(): JSX.Element {
  const [tab, setTab] = useState<"users" | "audit" | "jobs" | "master">("users");
  return (
    <>
      <PageHeader title="Administration" subtitle="Users and roles, audit trail, background jobs and master data" />
      <Tabs
        value={tab}
        onChange={setTab}
        items={[
          { id: "users", label: "Users & roles" },
          { id: "audit", label: "Audit log" },
          { id: "jobs", label: "Jobs" },
          { id: "master", label: "Master data" },
        ]}
      />
      {tab === "users" && <UsersTab />}
      {tab === "audit" && <AuditTab />}
      {tab === "jobs" && <JobsTab />}
      {tab === "master" && <MasterTab />}
    </>
  );
}
