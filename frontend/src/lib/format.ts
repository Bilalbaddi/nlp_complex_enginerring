export const CATEGORIES = [
  "water_supply",
  "drainage",
  "roads",
  "garbage",
  "streetlights",
  "encroachment",
  "public_health",
  "electricity",
  "parks",
  "noise",
] as const;

export const METRIC_LABELS: Record<string, string> = {
  complaints: "Complaints",
  resolution_rate: "Resolution rate (%)",
  sla_compliance_pct: "SLA compliance (%)",
  avg_resolution_hours: "Avg resolution (h)",
  median_resolution_hours: "Median resolution (h)",
  satisfaction: "Citizen satisfaction (1-5)",
  complaints_per_10k: "Complaints per 10k people",
  repeat_rate_pct: "Repeat complaints (%)",
  efficiency_score: "Efficiency score (0-100)",
  open_backlog: "Open backlog",
  gap_index: "Service Gap Index (0-100)",
};

export function label(s: string | null | undefined): string {
  if (!s) return "-";
  const t = s.replace(/_/g, " ");
  return t.charAt(0).toUpperCase() + t.slice(1);
}

export function num(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  return v.toLocaleString("en-IN", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function pct(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined ? "-" : `${num(v, digits)}%`;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
}

export function dateOnly(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleDateString("en-IN", { dateStyle: "medium" }) : "-";
}

/** "2026-06" -> "Jun 2026" */
export function monthLabel(m: string): string {
  const [y = 0, mo = 1] = m.split("-").map(Number);
  return new Date(Date.UTC(y, mo - 1, 1)).toLocaleDateString("en-IN", { month: "short", year: "numeric", timeZone: "UTC" });
}

export function addMonths(m: string, k: number): string {
  const [y = 0, mo = 1] = m.split("-").map(Number);
  const t = y * 12 + (mo - 1) + k;
  return `${String(Math.floor(t / 12)).padStart(4, "0")}-${String((t % 12) + 1).padStart(2, "0")}`;
}

export function monthRange(from: string, to: string): string[] {
  const out: string[] = [];
  for (let m = from; m <= to; m = addMonths(m, 1)) out.push(m);
  return out;
}

const GOOD = [69, 117, 180];
const MID = [255, 255, 191];
const BAD = [215, 48, 39];

/** Choropleth colour: blue (good) -> pale yellow -> red (bad); ColorBrewer RdYlBu, safe for common colour blindness. */
export function scaleColor(value: number | null, min: number, max: number, higherIsBetter: boolean): string {
  if (value === null) return "#9ca3af";
  const t = max > min ? (value - min) / (max - min) : 0.5;
  const bad = higherIsBetter ? 1 - t : t; // 0 = good, 1 = bad
  const [from, to, k] = bad < 0.5 ? [GOOD, MID, bad * 2] : [MID, BAD, (bad - 0.5) * 2];
  const c = [0, 1, 2].map((i) => Math.round((from[i] ?? 0) + ((to[i] ?? 0) - (from[i] ?? 0)) * k));
  return `rgb(${c.join(",")})`;
}

export function toCsv(rows: Record<string, unknown>[]): string {
  if (!rows.length) return "";
  const cols = Object.keys(rows[0] ?? {});
  const esc = (v: unknown): string => {
    const s = v === null || v === undefined ? "" : String(v);
    // prefix formula-looking cells so spreadsheets do not execute them
    const safe = /^[=+\-@]/.test(s) && Number.isNaN(Number(s)) ? `'${s}` : s;
    return /[",\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
  };
  return [cols.join(","), ...rows.map((r) => cols.map((c) => esc(r[c])).join(","))].join("\n");
}

export function downloadText(filename: string, text: string, mime = "text/csv"): void {
  const url = URL.createObjectURL(new Blob([text], { type: `${mime};charset=utf-8` }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
