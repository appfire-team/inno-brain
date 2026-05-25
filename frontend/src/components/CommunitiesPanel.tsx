import { useEffect, useState } from "react";
import { api, communityColor, type Community } from "../api";

type Props = {
  onFocusCommunity: (id: number) => void;
  onNodeClick: (label: string) => void;
};

export function CommunitiesPanel({ onFocusCommunity, onNodeClick }: Props) {
  const [communities, setCommunities] = useState<Community[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  useEffect(() => {
    api.communities()
      .then((d) => setCommunities(d.communities))
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="empty-state"><div className="spinner" /> Loading communities…</div>;
  if (error) return <div className="empty-state"><h2>Failed to load communities</h2><p>{error}</p></div>;
  if (communities.length === 0) {
    return <div className="empty-state"><h2>No communities</h2><p>Upload documents first.</p></div>;
  }

  const toggle = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="communities-panel">
      <p className="muted-note">
        {communities.length} communities · click a community to expand its members
      </p>
      <ul className="community-list">
        {communities.map((c) => {
          const isOpen = expanded.has(c.id);
          return (
            <li key={c.id} className="community-card">
              <div className="community-header" onClick={() => toggle(c.id)}>
                <span className="legend-swatch" style={{ background: communityColor(c.id) }} />
                <span className="community-name">{c.label}</span>
                <span className="community-meta">
                  {c.size} nodes
                  {c.cohesion != null && <> · cohesion {c.cohesion.toFixed(2)}</>}
                </span>
                <button
                  className="btn-secondary small"
                  onClick={(e) => { e.stopPropagation(); onFocusCommunity(c.id); }}
                >
                  show in graph
                </button>
              </div>
              {isOpen && (
                <ul className="community-members">
                  {c.nodes.slice(0, 50).map((n) => (
                    <li key={n.id}>
                      <button className="link-btn" onClick={() => onNodeClick(n.label)}>{n.label}</button>
                      {n.source_file && <span className="node-src">{n.source_file}</span>}
                    </li>
                  ))}
                  {c.nodes.length > 50 && <li className="muted-note">+ {c.nodes.length - 50} more…</li>}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
