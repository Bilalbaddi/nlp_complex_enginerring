import { afterEach, describe, expect, it, vi } from "vitest";
import { addMonths, label, monthLabel, monthRange, num, pct, scaleColor, toCsv } from "./format";
import { navFor } from "./nav";
import { qs } from "./hooks";
import { streamChat, type ChatEvent } from "./sse";

describe("format", () => {
  it("does month arithmetic across year boundaries", () => {
    expect(addMonths("2025-11", 3)).toBe("2026-02");
    expect(addMonths("2026-01", -1)).toBe("2025-12");
    expect(monthRange("2025-11", "2026-02")).toEqual(["2025-11", "2025-12", "2026-01", "2026-02"]);
    expect(monthLabel("2026-06")).toBe("Jun 2026");
  });

  it("formats numbers and missing values", () => {
    expect(num(null)).toBe("-");
    expect(num(1234567)).toBe("12,34,567"); // Indian digit grouping
    expect(pct(12.345)).toBe("12.3%");
    expect(label("public_health")).toBe("Public health");
  });

  it("neutralises spreadsheet formulas and quotes CSV cells", () => {
    const csv = toCsv([{ a: "=HYPERLINK(\"x\")", b: 'say "hi", ok', c: -5, d: null }]);
    const row = csv.split("\n")[1] ?? "";
    expect(row.startsWith("\"'=HYPERLINK")).toBe(true); // formula prefixed with a quote
    expect(row).toContain('"say ""hi"", ok"');
    expect(row).toContain(",-5,"); // real negative numbers are untouched
    expect(toCsv([])).toBe("");
  });

  it("colours the worst ward redder than the best, in both directions", () => {
    const red = (c: string): number => Number(/rgb\((\d+)/.exec(c)?.[1]);
    expect(red(scaleColor(100, 0, 100, false))).toBeGreaterThan(red(scaleColor(0, 0, 100, false))); // higher = worse -> redder
    expect(red(scaleColor(0, 0, 100, true))).toBeGreaterThan(red(scaleColor(100, 0, 100, true))); // higher = better -> bluer
    expect(scaleColor(null, 0, 1, true)).toBe("#9ca3af");
  });
});

describe("navigation and query strings", () => {
  it("gives each role only the pages it may use", () => {
    const hrefs = (r: Parameters<typeof navFor>[0]): string[] => navFor(r).map((n) => n.href);
    expect(hrefs("ward_officer")).not.toContain("/reports");
    expect(hrefs("ward_officer")).not.toContain("/admin");
    expect(hrefs("analyst")).toContain("/ingestion");
    expect(hrefs("commissioner")).not.toContain("/ingestion");
    expect(hrefs("admin")).toHaveLength(13);
  });

  it("builds query strings without empty values", () => {
    expect(qs({ a: 1, b: "", c: null, d: undefined, e: "x y" })).toBe("?a=1&e=x+y");
    expect(qs({})).toBe("");
  });
});

describe("streamChat (SSE parser)", () => {
  afterEach(() => vi.unstubAllGlobals());

  function stream(chunks: string[]): Response {
    const enc = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        chunks.forEach((x) => c.enqueue(enc.encode(x)));
        c.close();
      },
    });
    return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
  }

  it("reassembles events split across network chunks and stops at [DONE]", async () => {
    const ev = (o: object): string => `data: ${JSON.stringify(o)}\n\n`;
    const payload = ev({ type: "tool", name: "rank_wards", args: {} }) + ev({ type: "token", text: "Hello " }) + ev({ type: "token", text: "world" });
    const cut = payload.indexOf("Hello") + 3; // split in the middle of a JSON frame
    vi.stubGlobal("fetch", vi.fn(async () => stream([payload.slice(0, cut), payload.slice(cut) + ev({ type: "done", tools_used: [], label: { ai_generated: true } }) + "data: [DONE]\n\n" + ev({ type: "token", text: "ignored" })])));
    const got: ChatEvent[] = [];
    await streamChat("q", [], (e) => got.push(e));
    expect(got.map((e) => e.type)).toEqual(["tool", "token", "token", "done"]);
    expect(got.filter((e): e is Extract<ChatEvent, { type: "token" }> => e.type === "token").map((e) => e.text).join("")).toBe("Hello world");
  });

  it("turns HTTP errors into an error event (429 gets a friendly message)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ success: false, error: { message: "slow down" } }), { status: 429 })));
    const got: ChatEvent[] = [];
    await streamChat("q", [], (e) => got.push(e));
    expect(got).toHaveLength(1);
    expect(got[0]).toMatchObject({ type: "error" });
    expect((got[0] as { message: string }).message).toMatch(/wait a minute/);
  });

  it("ignores malformed frames instead of crashing", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => stream(["data: {not json\n\n", 'data: {"type":"token","text":"ok"}\n\n', "data: [DONE]\n\n"])));
    const got: ChatEvent[] = [];
    await streamChat("q", [], (e) => got.push(e));
    expect(got).toEqual([{ type: "token", text: "ok" }]);
  });
});
