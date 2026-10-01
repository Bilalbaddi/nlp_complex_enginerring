import { API_URL, tokens } from "./api";
import type { AiLabel } from "./types";

export type ChatEvent =
  | { type: "tool"; name: string; args: Record<string, unknown> }
  | { type: "token"; text: string }
  | { type: "done"; tools_used: { name: string; args: Record<string, unknown> }[]; label: AiLabel }
  | { type: "error"; message: string };

/** POST + Server-Sent Events (EventSource cannot POST or send auth headers). Calls onEvent for each parsed event. */
export async function streamChat(
  message: string,
  history: { role: "user" | "assistant"; content: string }[],
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const res = await fetch(`${API_URL}/assistant/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${tokens.access ?? ""}` },
    body: JSON.stringify({ message, history }),
    signal,
  });
  if (!res.ok || !res.body) {
    let msg = res.statusText;
    try {
      const body = (await res.json()) as { error?: { message?: string } };
      msg = body.error?.message ?? msg;
    } catch {
      // not JSON
    }
    onEvent({ type: "error", message: res.status === 429 ? "Too many questions, wait a minute and try again." : msg });
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";
    for (const part of parts) {
      const line = part.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      const payload = line.slice(6);
      if (payload === "[DONE]") return;
      try {
        onEvent(JSON.parse(payload) as ChatEvent);
      } catch {
        // ignore a malformed frame
      }
    }
  }
}
