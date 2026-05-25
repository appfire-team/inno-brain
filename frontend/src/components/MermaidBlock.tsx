import { useEffect, useId, useRef, useState } from "react";
import mermaid from "mermaid";

let initialized = false;
function ensureInit() {
  if (initialized) return;
  mermaid.initialize({
    startOnLoad: false,
    theme: "dark",
    securityLevel: "strict",
    fontFamily: "ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif",
    themeVariables: {
      background: "transparent",
      primaryColor: "#1f2937",
      primaryTextColor: "#e5e7eb",
      primaryBorderColor: "#4b5563",
      lineColor: "#9ca3af",
      secondaryColor: "#374151",
      tertiaryColor: "#111827",
      clusterBkg: "#111827",
      clusterBorder: "#374151",
    },
  });
  initialized = true;
}

type Props = { code: string };

export function MermaidBlock({ code }: Props) {
  const reactId = useId();
  const id = `mmd-${reactId.replace(/[^a-zA-Z0-9]/g, "")}`;
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);
  const [svg, setSvg] = useState<string>("");

  useEffect(() => {
    ensureInit();
    let cancelled = false;
    (async () => {
      try {
        const { svg: rendered } = await mermaid.render(id, code);
        if (!cancelled) {
          setSvg(rendered);
          setError(null);
        }
      } catch (e: any) {
        if (!cancelled) setError(e?.message ?? String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [code, id]);

  if (error) {
    return (
      <div className="md-mermaid md-mermaid-error" title={error}>
        <div className="md-mermaid-error-msg">Diagram failed to render</div>
        <pre className="md-code-block"><code>{code}</code></pre>
      </div>
    );
  }

  return <div ref={ref} className="md-mermaid" dangerouslySetInnerHTML={{ __html: svg }} />;
}
