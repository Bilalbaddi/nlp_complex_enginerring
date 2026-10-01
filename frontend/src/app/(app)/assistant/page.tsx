"use client";

import { Send } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Markdown } from "@/components/Markdown";
import { AiBadge, Badge, Button, Card, ErrorBox, Input, PageHeader, Spinner, Tabs } from "@/components/ui";
import { api } from "@/lib/api";
import { streamChat } from "@/lib/sse";
import type { AiLabel, RagAnswer } from "@/lib/types";

interface Msg {
  role: "user" | "assistant";
  content: string;
  tools?: { name: string; args: Record<string, unknown> }[];
  label?: AiLabel;
  sources?: RagAnswer["sources"];
  error?: boolean;
  pending?: boolean;
}

const DATA_SUGGESTIONS = [
  "Which wards had the worst drainage performance last monsoon?",
  "What emerging issues do we have right now and where?",
  "How is SLA compliance trending versus last year?",
  "Which departments will be short of crew capacity in the next 3 months?",
];
const DOC_SUGGESTIONS = ["What is the target time to repair a pothole?", "What does the grievance policy say about escalation?", "What are the monsoon preparedness requirements for drains?"];

function ToolChips({ tools }: { tools: NonNullable<Msg["tools"]> }): JSX.Element {
  return (
    <div className="mb-2 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
      <span>Data sources:</span>
      {tools.map((t, i) => (
        <Badge key={i} tone="neutral" title={JSON.stringify(t.args)}>
          {t.name.replace(/_/g, " ")}
        </Badge>
      ))}
    </div>
  );
}

export default function AssistantPage(): JSX.Element {
  const [mode, setMode] = useState<"data" | "docs">("data");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth" });
  }, [msgs]);
  useEffect(() => () => abort.current?.abort(), []);

  const patchLast = (fn: (m: Msg) => Msg): void => setMsgs((all) => all.map((m, i) => (i === all.length - 1 ? fn(m) : m)));

  async function send(text: string): Promise<void> {
    const q = text.trim();
    if (!q || busy) return;
    setInput("");
    setBusy(true);
    const history = msgs.filter((m) => !m.error && !m.pending).map((m) => ({ role: m.role, content: m.content }));
    setMsgs((all) => [...all, { role: "user", content: q }, { role: "assistant", content: "", pending: true }]);
    try {
      if (mode === "docs") {
        const r = await api<RagAnswer>("/insights/ask-documents", { method: "POST", body: JSON.stringify({ question: q }) });
        patchLast(() => ({ role: "assistant", content: r.answer, label: r.label, sources: r.sources }));
      } else {
        abort.current = new AbortController();
        await streamChat(
          q,
          history.slice(-12),
          (e) => {
            if (e.type === "tool") patchLast((m) => ({ ...m, tools: [...(m.tools ?? []), { name: e.name, args: e.args }] }));
            else if (e.type === "token") patchLast((m) => ({ ...m, content: m.content + e.text }));
            else if (e.type === "done") patchLast((m) => ({ ...m, pending: false, label: e.label }));
            else if (e.type === "error") patchLast((m) => ({ ...m, pending: false, error: true, content: e.message }));
          },
          abort.current.signal,
        );
      }
    } catch (err) {
      if ((err as Error).name !== "AbortError") patchLast((m) => ({ ...m, error: true, content: err instanceof Error ? err.message : "Request failed" }));
    } finally {
      patchLast((m) => ({ ...m, pending: false }));
      setBusy(false);
    }
  }

  const suggestions = mode === "data" ? DATA_SUGGESTIONS : DOC_SUGGESTIONS;
  return (
    <>
      <PageHeader title="AI assistant" subtitle="Ask questions in plain language. Answers use only the platform's analytics functions or policy documents; the AI never writes SQL." />
      <Tabs
        value={mode}
        onChange={(m) => {
          setMode(m);
          setMsgs([]);
        }}
        items={[
          { id: "data", label: "Ask the data" },
          { id: "docs", label: "Ask the policy documents" },
        ]}
      />
      <Card className="flex min-h-[26rem] flex-col">
        <div className="flex-1 space-y-4 overflow-y-auto" aria-live="polite">
          {msgs.length === 0 && (
            <div>
              <p className="mb-2 text-sm text-muted-foreground">Try one of these:</p>
              <div className="flex flex-wrap gap-2">
                {suggestions.map((s) => (
                  <button key={s} onClick={() => void send(s)} className="rounded-full border border-border px-3 py-1.5 text-left text-sm hover:bg-muted">
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {msgs.map((m, i) =>
            m.role === "user" ? (
              <div key={i} className="ml-auto max-w-[85%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">
                {m.content}
              </div>
            ) : (
              <div key={i} className="max-w-[92%] rounded-lg border border-border bg-muted/40 p-3">
                {m.tools && m.tools.length > 0 && <ToolChips tools={m.tools} />}
                {m.error ? (
                  <ErrorBox message={m.content} />
                ) : m.content ? (
                  <Markdown text={m.content} />
                ) : (
                  <p className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Spinner /> {m.tools?.length ? "Analysing results…" : "Looking up the data…"}
                  </p>
                )}
                {m.sources && m.sources.length > 0 && (
                  <ul className="mt-2 space-y-1 border-t border-border pt-2 text-xs text-muted-foreground">
                    {m.sources.map((s) => (
                      <li key={s.n}>
                        <strong>[{s.n}]</strong> {s.title}, p.{s.page ?? "-"} ({s.retrieval} match {s.score}): “{s.snippet}…”
                      </li>
                    ))}
                  </ul>
                )}
                {m.label && !m.pending && (
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    <AiBadge label={m.label} />
                    {m.label.as_of && <span className="text-xs text-muted-foreground">data as of {m.label.as_of}</span>}
                  </div>
                )}
              </div>
            ),
          )}
          <div ref={end} />
        </div>
        <form
          className="mt-4 flex gap-2 border-t border-border pt-3"
          onSubmit={(e) => {
            e.preventDefault();
            void send(input);
          }}
        >
          <Input aria-label="Your question" value={input} onChange={(e) => setInput(e.target.value)} placeholder={mode === "data" ? "Ask about KPIs, wards, forecasts…" : "Ask about a policy…"} maxLength={1000} disabled={busy} />
          <Button type="submit" disabled={busy || input.trim().length < 3}>
            <Send className="h-4 w-4" /> Send
          </Button>
        </form>
      </Card>
      <p className="mt-2 text-xs text-muted-foreground">AI-generated answers can be wrong. Check the data sources shown with each answer before acting. Personal data is removed before anything is sent to the AI service.</p>
    </>
  );
}
