import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D, { type ForceGraphMethods } from "react-force-graph-2d";
import { api, communityColor, type RawGraph } from "../api";

type Props = {
  onNodeClick: (label: string) => void;
  focusedCommunity?: number | null;
};

type FGNode = {
  id: string;
  label: string;
  community?: number;
  community_label?: string;
  source_file?: string;
  degree?: number;
};

type FGLink = {
  source: string;
  target: string;
  relation?: string;
  confidence?: string;
};

export function GraphPanel({ onNodeClick, focusedCommunity }: Props) {
  const fgRef = useRef<ForceGraphMethods<FGNode, FGLink> | undefined>(undefined);
  const containerRef = useRef<HTMLDivElement>(null);
  const [data, setData] = useState<{ nodes: FGNode[]; links: FGLink[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [size, setSize] = useState({ w: 800, h: 600 });
  const [highlightId, setHighlightId] = useState<string | null>(null);
  const [hiddenCommunities, setHiddenCommunities] = useState<Set<number>>(new Set());
  const [hiddenSources, setHiddenSources] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");

  useEffect(() => {
    setLoading(true);
    api.graph()
      .then((g: RawGraph) => {
        const nodeMap = new Map<string, FGNode>();
        for (const n of g.nodes) {
          nodeMap.set(n.id, {
            id: n.id,
            label: n.label ?? n.id,
            community: n.community,
            community_label: n.community_label,
            source_file: n.source_file,
            degree: 0,
          });
        }
        const links: FGLink[] = [];
        for (const l of g.links) {
          const s = typeof l.source === "string" ? l.source : (l.source as any).id;
          const t = typeof l.target === "string" ? l.target : (l.target as any).id;
          if (nodeMap.has(s) && nodeMap.has(t)) {
            links.push({ source: s, target: t, relation: l.relation, confidence: l.confidence });
            nodeMap.get(s)!.degree! += 1;
            nodeMap.get(t)!.degree! += 1;
          }
        }
        setData({ nodes: Array.from(nodeMap.values()), links });
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver(() => {
      if (containerRef.current) {
        setSize({
          w: containerRef.current.clientWidth,
          h: containerRef.current.clientHeight,
        });
      }
    });
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    if (focusedCommunity == null || !data) return;
    const inComm = data.nodes.filter((n) => n.community === focusedCommunity);
    if (inComm.length === 0) return;
    setHighlightId(null);
    setTimeout(() => fgRef.current?.zoomToFit(400, 80, (n: FGNode) => n.community === focusedCommunity), 200);
  }, [focusedCommunity, data]);

  const communityList = useMemo(() => {
    if (!data) return [];
    const seen = new Map<number, string>();
    for (const n of data.nodes) {
      if (n.community != null && !seen.has(n.community)) {
        seen.set(n.community, n.community_label ?? `Community ${n.community}`);
      }
    }
    return Array.from(seen.entries()).sort((a, b) => a[0] - b[0]);
  }, [data]);

  const sourceList = useMemo(() => {
    if (!data) return [];
    const seen = new Map<string, number>();
    for (const n of data.nodes) {
      if (n.source_file) seen.set(n.source_file, (seen.get(n.source_file) ?? 0) + 1);
    }
    return Array.from(seen.entries()).sort((a, b) => b[1] - a[1]);
  }, [data]);

  // Filter logic — applied at draw time via opacity, so positions stay stable.
  const isVisible = (n: FGNode): boolean => {
    if (n.community != null && hiddenCommunities.has(n.community)) return false;
    if (n.source_file && hiddenSources.has(n.source_file)) return false;
    if (search.trim()) {
      const q = search.toLowerCase();
      if (!n.label.toLowerCase().includes(q)) return false;
    }
    return true;
  };

  const toggleCommunity = (id: number) => {
    setHiddenCommunities((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  const toggleSource = (s: string) => {
    setHiddenSources((prev) => {
      const next = new Set(prev);
      next.has(s) ? next.delete(s) : next.add(s);
      return next;
    });
  };

  const zoomBy = (factor: number) => {
    const fg = fgRef.current;
    if (!fg) return;
    fg.zoom(fg.zoom() * factor, 300);
  };

  const zoomReset = () => fgRef.current?.zoomToFit(400, 60);

  if (loading) return <div className="empty-state"><div className="spinner" /> Loading graph…</div>;
  if (error) return <div className="empty-state"><h2>Failed to load graph</h2><p>{error}</p></div>;
  if (!data || data.nodes.length === 0) {
    return <div className="empty-state"><h2>Graph is empty</h2><p>Upload at least one document.</p></div>;
  }

  return (
    <div className="graph-panel">
      <div className="graph-canvas" ref={containerRef}>
        <ForceGraph2D
          ref={fgRef as any}
          graphData={data}
          width={size.w}
          height={size.h}
          backgroundColor="#0b0f1a"
          nodeRelSize={4}
          nodeVal={(n: FGNode) => 1 + Math.log2(1 + (n.degree ?? 0))}
          nodeVisibility={(n: FGNode) => isVisible(n)}
          linkVisibility={(l: any) => {
            const s = typeof l.source === "object" ? l.source : data.nodes.find((n) => n.id === l.source);
            const t = typeof l.target === "object" ? l.target : data.nodes.find((n) => n.id === l.target);
            return s && t ? isVisible(s as FGNode) && isVisible(t as FGNode) : true;
          }}
          nodeColor={(n: FGNode) => {
            if (focusedCommunity != null && n.community !== focusedCommunity) return "#334155";
            if (highlightId === n.id) return "#fff";
            return communityColor(n.community);
          }}
          linkColor={(l: any) => {
            const conf = (l.confidence as string) || "";
            if (conf === "EXTRACTED") return "rgba(52, 211, 153, 0.35)";
            if (conf === "INFERRED") return "rgba(251, 191, 36, 0.35)";
            if (conf === "AMBIGUOUS") return "rgba(251, 113, 133, 0.35)";
            return "rgba(148, 163, 184, 0.18)";
          }}
          linkWidth={(l: any) => (l.confidence === "EXTRACTED" ? 1.2 : 0.6)}
          nodeCanvasObjectMode={() => "after"}
          nodeCanvasObject={(node: any, ctx, globalScale) => {
            const deg = node.degree ?? 0;
            if (globalScale < 1.2 && deg < 4) return;
            const label: string = node.label ?? node.id;
            const truncated = label.length > 40 ? label.slice(0, 38) + "…" : label;
            const fontSize = 10 / globalScale;
            ctx.font = `${fontSize}px -apple-system, sans-serif`;
            ctx.fillStyle = "rgba(230, 235, 245, 0.85)";
            ctx.textAlign = "left";
            ctx.textBaseline = "middle";
            ctx.fillText(truncated, node.x + 6, node.y);
          }}
          onNodeHover={(n: FGNode | null) => setHighlightId(n?.id ?? null)}
          onNodeClick={(n: FGNode) => onNodeClick(n.label)}
          cooldownTicks={120}
          d3VelocityDecay={0.32}
        />
        <div className="graph-zoom-controls">
          <button onClick={() => zoomBy(1.4)} title="Zoom in">+</button>
          <button onClick={() => zoomBy(0.7)} title="Zoom out">−</button>
          <button onClick={zoomReset} title="Fit to view">⤢</button>
        </div>
      </div>

      <div className="graph-legend">
        <section className="legend-section">
          <input
            className="text-input"
            placeholder="Search node labels…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {search.trim() && (
            <div className="muted-note" style={{ marginTop: 6 }}>
              {data.nodes.filter((n) => isVisible(n)).length} match{data.nodes.filter((n) => isVisible(n)).length === 1 ? "" : "es"}
            </div>
          )}
        </section>

        <section className="legend-section">
          <h3>Communities</h3>
          <ul>
            {communityList.map(([id, label]) => (
              <li key={id} className="filter-row">
                <input
                  type="checkbox"
                  checked={!hiddenCommunities.has(id)}
                  onChange={() => toggleCommunity(id)}
                />
                <span className="legend-swatch" style={{ background: communityColor(id) }} />
                <span className="filter-label">{label}</span>
              </li>
            ))}
          </ul>
        </section>

        {sourceList.length > 0 && (
          <section className="legend-section">
            <h3>Sources</h3>
            <ul>
              {sourceList.map(([src, count]) => (
                <li key={src} className="filter-row">
                  <input
                    type="checkbox"
                    checked={!hiddenSources.has(src)}
                    onChange={() => toggleSource(src)}
                  />
                  <span className="filter-label" title={src}>
                    {src.length > 36 ? src.slice(0, 34) + "…" : src}
                  </span>
                  <span className="source-count">{count}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <div className="legend-hint">scroll to zoom · drag to pan · click a node to explain</div>
      </div>
    </div>
  );
}
