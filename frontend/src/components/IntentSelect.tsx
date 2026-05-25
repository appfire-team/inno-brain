import { useEffect, useMemo, useRef, useState } from "react";

type IntentGroup = { label: string; intents: Record<string, string> };

type Props = {
  value: string | null;
  onChange: (intent: string | null) => void;
  groups: IntentGroup[];
  flatLabels: Record<string, string>;
  placeholder?: string;
  emptyLabel?: string;
  className?: string;
};

// Custom grouped dropdown — we can't reliably style native <optgroup> across
// macOS Safari + Chrome, so we render our own popover that matches the page
// theme exactly. Returns the same kind of (id | null) value a <select> would.
export function IntentSelect({
  value,
  onChange,
  groups,
  flatLabels,
  placeholder = "Pick one…",
  emptyLabel = "— none —",
  className,
}: Props) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const items = useMemo(() => {
    if (groups && groups.length > 0) return groups;
    return [{ label: "", intents: flatLabels }];
  }, [groups, flatLabels]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!containerRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const currentLabel = value ? flatLabels[value] ?? value : null;

  const pick = (next: string | null) => {
    onChange(next);
    setOpen(false);
  };

  return (
    <div className={`intent-select ${className ?? ""}`} ref={containerRef}>
      <button
        type="button"
        className="intent-trigger"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span className={currentLabel ? "intent-value" : "intent-placeholder"}>
          {currentLabel ?? placeholder}
        </span>
        <span className="intent-caret" aria-hidden>▾</span>
      </button>
      {open && (
        <div className="intent-popover" role="listbox">
          <button
            type="button"
            className={`intent-option intent-option-none ${value == null ? "selected" : ""}`}
            onClick={() => pick(null)}
          >
            {emptyLabel}
          </button>
          {items.map((g) => (
            <div key={g.label || "_"} className="intent-group">
              {g.label && <div className="intent-group-label">{g.label}</div>}
              {Object.entries(g.intents).map(([k, v]) => (
                <button
                  type="button"
                  key={k}
                  className={`intent-option ${value === k ? "selected" : ""}`}
                  onClick={() => pick(k)}
                  role="option"
                  aria-selected={value === k}
                >
                  {v}
                </button>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
