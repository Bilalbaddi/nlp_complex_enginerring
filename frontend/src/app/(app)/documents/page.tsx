"use client";

import { Download, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Async, Badge, Button, Card, CardHeader, Empty, ErrorBox, Field, Input, PageHeader, Pagination, Select, Spinner, Table } from "@/components/ui";
import { ApiError, API_URL, api, tokens } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { dateOnly, label } from "@/lib/format";
import { qs, useApi } from "@/lib/hooks";
import type { DocumentItem, IngestionLog, Page } from "@/lib/types";

export default function DocumentsPage(): JSX.Element {
  const { user } = useAuth();
  const canUpload = user?.role === "analyst" || user?.role === "admin";
  const [page, setPage] = useState(1);
  const docs = useApi<Page<DocumentItem>>(`/documents${qs({ page, page_size: 15 })}`);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [type, setType] = useState("policy");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function upload(e: React.FormEvent): Promise<void> {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setMsg(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("title", title);
      fd.append("doc_type", type);
      const res = await fetch(`${API_URL}/documents`, { method: "POST", headers: { Authorization: `Bearer ${tokens.access ?? ""}` }, body: fd });
      const body = (await res.json()) as { success: boolean; data: IngestionLog | null; error: { message: string } | null };
      if (!res.ok || !body.success) throw new ApiError(res.status, "upload", body.error?.message ?? "Upload failed");
      setMsg({ ok: true, text: "Uploaded. The document is being processed in the background; refresh in a few seconds." });
      setFile(null);
      setTitle("");
      window.setTimeout(docs.reload, 4000);
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Upload failed" });
    } finally {
      setBusy(false);
    }
  }

  async function download(id: number): Promise<void> {
    try {
      const r = await api<{ url: string }>(`/documents/${id}/download`);
      window.open(r.url, "_blank", "noopener");
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Download failed" });
    }
  }

  async function remove(d: DocumentItem): Promise<void> {
    if (!window.confirm(`Delete “${d.title}”? This cannot be undone.`)) return;
    try {
      await api(`/documents/${d.id}`, { method: "DELETE" });
      docs.reload();
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : "Delete failed" });
    }
  }

  return (
    <>
      <PageHeader title="Policy documents" subtitle="Policies and reports used by the AI assistant to answer questions with citations" />
      {canUpload && (
        <Card className="mb-4">
          <CardHeader title="Upload a document" subtitle="PDF, DOCX or TXT, up to 15 MB. Text is extracted and split into searchable chunks." />
          <form onSubmit={(e) => void upload(e)} className="grid gap-3 sm:grid-cols-4">
            <Field label="File">
              <Input type="file" accept=".pdf,.docx,.txt,.md" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="h-auto py-1.5" />
            </Field>
            <Field label="Title">
              <Input value={title} onChange={(e) => setTitle(e.target.value)} minLength={3} maxLength={255} required />
            </Field>
            <Field label="Type">
              <Select value={type} onChange={(e) => setType(e.target.value)}>
                <option value="policy">Policy</option>
                <option value="report">Report</option>
              </Select>
            </Field>
            <div className="flex items-end">
              <Button type="submit" disabled={busy || !file || title.length < 3}>
                {busy && <Spinner />}Upload
              </Button>
            </div>
          </form>
        </Card>
      )}
      {msg && (
        <div className="mb-3">
          {msg.ok ? <p role="status" className="rounded-md border border-success/40 p-3 text-sm text-success">{msg.text}</p> : <ErrorBox message={msg.text} />}
        </div>
      )}
      <Card>
        <Async state={docs}>
          {(d) =>
            d.items.length === 0 ? (
              <Empty>No documents yet.</Empty>
            ) : (
              <>
                <Table head={["Title", "Type", "File", "Chunks", "Added", ""]}>
                  {d.items.map((x) => (
                    <tr key={x.id}>
                      <td className="font-medium">{x.title}</td>
                      <td><Badge tone="info">{label(x.doc_type)}</Badge></td>
                      <td className="text-muted-foreground">{x.source_filename}</td>
                      <td className="tabular-nums">{x.num_chunks}</td>
                      <td>{dateOnly(x.created_at)}</td>
                      <td className="whitespace-nowrap text-right">
                        <Button size="sm" variant="ghost" aria-label={`Download ${x.title}`} onClick={() => void download(x.id)}>
                          <Download className="h-4 w-4" />
                        </Button>
                        {user?.role === "admin" && (
                          <Button size="sm" variant="ghost" aria-label={`Delete ${x.title}`} onClick={() => void remove(x)}>
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </Table>
                <Pagination page={d.meta.page} pages={d.meta.pages} total={d.meta.total} onPage={setPage} />
              </>
            )
          }
        </Async>
        <p className="mt-3 text-sm text-muted-foreground">To question these documents, use <Link className="text-primary underline" href="/assistant">AI assistant → Ask the policy documents</Link>.</p>
      </Card>
    </>
  );
}
