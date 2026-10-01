"use client";

export function Markdown({ text }: { text: string }): JSX.Element {
  // The brief uses only headings, bullets, numbered lists and bold: render those without a markdown dependency.
  const bold = (s: string): React.ReactNode[] => s.split(/(\*\*[^*]+\*\*)/g).map((p, i) => (p.startsWith("**") ? <strong key={i}>{p.slice(2, -2)}</strong> : p));
  return (
    <div className="space-y-1.5 text-sm leading-relaxed">
      {text.split("\n").map((line, i) => {
        if (line.startsWith("## ")) return <h3 key={i} className="mt-3 text-sm font-semibold text-primary">{line.slice(3)}</h3>;
        if (/^\s*[-*•] /.test(line)) return <p key={i} className="pl-4 -indent-3">• {bold(line.replace(/^\s*[-*•] /, ""))}</p>;
        if (/^\s*\d+\. /.test(line)) return <p key={i} className="pl-4 -indent-4">{bold(line.trim())}</p>;
        return line.trim() ? <p key={i}>{bold(line)}</p> : null;
      })}
    </div>
  );
}
