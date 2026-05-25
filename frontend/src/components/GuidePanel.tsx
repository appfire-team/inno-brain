import { useState } from "react";
import { MarkdownView } from "./MarkdownView";
import playbooksGuide from "../guides/playbooks.md?raw";
import simulationGuide from "../guides/simulation.md?raw";
import rubricsAndIntentsGuide from "../guides/rubrics-and-intents.md?raw";

type GuideEntry = {
  id: string;
  title: string;
  blurb: string;
  body: string;
};

const GUIDES: GuideEntry[] = [
  {
    id: "playbooks",
    title: "Playbooks",
    blurb: "Multi-step workflows that produce typed Artifacts. Chain them together: Discover → PRD → Launch.",
    body: playbooksGuide,
  },
  {
    id: "simulation",
    title: "Simulation pipeline",
    blurb: "How the quick Simulate and ForeSight pipelines work, and how to read what comes back.",
    body: simulationGuide,
  },
  {
    id: "rubrics-and-intents",
    title: "Rubrics & Intents",
    blurb: "How intents shape answer style, and how rubrics inject company-specific evaluation rules.",
    body: rubricsAndIntentsGuide,
  },
];

export function GuidePanel() {
  const [active, setActive] = useState<string>(GUIDES[0]?.id ?? "");
  const current = GUIDES.find((g) => g.id === active) ?? GUIDES[0];

  const download = () => {
    if (!current) return;
    const blob = new Blob([current.body], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${current.id}.md`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="guide-panel">
      <aside className="guide-sidebar">
        <h3>Guides</h3>
        <ul>
          {GUIDES.map((g) => (
            <li key={g.id} className={active === g.id ? "active" : ""}>
              <button onClick={() => setActive(g.id)}>
                <div className="guide-item-title">{g.title}</div>
                <div className="guide-item-blurb">{g.blurb}</div>
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <main className="guide-main">
        {current && (
          <>
            <header className="guide-head">
              <h2>{current.title}</h2>
              <button className="btn-secondary small" onClick={download}>
                Download .md
              </button>
            </header>
            <MarkdownView className="guide-body">{current.body}</MarkdownView>
          </>
        )}
      </main>
    </div>
  );
}
