// A Mermaid diagram in an Assistant answer. The library is loaded on first use and runs
// with securityLevel "strict": it escapes label text and sanitizes the SVG it returns, and
// click handlers and raw HTML labels are off. A diagram that does not parse (a reply still
// streaming, or a model's mistake) shows its source as code instead.
import { useEffect, useId, useRef, useState } from "react";

import { cn } from "../lib/utils";
import { monoClasses } from "./parts";

type Mermaid = typeof import("mermaid").default;

let loading: Promise<Mermaid> | null = null;

function loadMermaid(): Promise<Mermaid> {
  loading ??= import("mermaid").then(({ default: mermaid }) => {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: "neutral",
      flowchart: { htmlLabels: false },
    });
    return mermaid;
  });
  return loading;
}

/** How long the source must stay unchanged before it is drawn (a streaming reply grows). */
const SETTLE_MS = 300;

export function MermaidDiagram({ code }: { code: string }) {
  const id = `mermaid-${useId().replace(/[^A-Za-z0-9]/g, "")}`;
  const holder = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  const [drawn, setDrawn] = useState(false);

  useEffect(() => {
    let current = true;
    const timer = setTimeout(() => {
      loadMermaid()
        .then((mermaid) => mermaid.render(id, code))
        .then(({ svg }) => {
          if (!current || !holder.current) return;
          holder.current.innerHTML = svg; // strict mode's sanitized SVG, never the reply's text
          setFailed(false);
          setDrawn(true);
        })
        .catch(() => {
          // A failed render can leave a stray error node in the page.
          document.getElementById(`d${id}`)?.remove();
          if (current) setFailed(true);
        });
    }, SETTLE_MS);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [code, id]);

  return (
    <figure className="my-2 overflow-x-auto rounded-md border border-border bg-surface-raised p-3">
      <div
        ref={holder}
        role="img"
        aria-label="Diagram"
        className={cn("flex justify-center", (failed || !drawn) && "hidden")}
      />
      {(failed || !drawn) && (
        <pre className={cn("overflow-x-auto leading-relaxed text-foreground", monoClasses)}>
          {code}
        </pre>
      )}
    </figure>
  );
}
