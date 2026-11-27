import { Issue } from "../api";
import { Icon } from "./icons";

export const scoreTone = (n: number) => (n >= 90 ? "ok" : n >= 75 ? "warn" : "err");

export function ScoreRing({ score, size = 44, busy }: { score: number | null; size?: number; busy?: boolean }) {
  const tone = score == null ? "mute" : scoreTone(score);
  return (
    <span className={`score-ring ring-${tone}`} style={{ width: size, height: size, fontSize: size * 0.32 }}>
      {busy ? <span className="spinner" /> : score == null ? "–" : Math.round(score)}
    </span>
  );
}

export function ScoreHistory({ items, current, selected, onSelect }: {
  items: { version: number; overall: number }[]; current?: number | null;
  selected?: number | null; onSelect?: (version: number) => void;
}) {
  if (items.length < 2) return null;
  return (
    <div className="history" aria-label="Score per version. Click a version to view it">
      {items.map((h, i) => (
        <span key={h.version} className="hstack" style={{ gap: 4 }}>
          {i > 0 && <Icon name="arrowRight" size={11} className="faint" />}
          <button type="button"
            className={`hist-chip hist-${scoreTone(h.overall)}${h.version === current ? " hist-now" : ""}${h.version === selected ? " hist-sel" : ""}`}
            title={h.version === current ? `Version ${h.version} (current)` : `View version ${h.version}`}
            aria-pressed={h.version === selected} onClick={() => onSelect?.(h.version)} disabled={!onSelect}>
            v{h.version} · {Math.round(h.overall)}
          </button>
        </span>
      ))}
    </div>
  );
}

export function IssueList({ issues, compact }: { issues: Issue[]; compact?: boolean }) {
  if (!issues.length) return null;
  return (
    <ul className="issue-list">
      {issues.map((i, k) => (
        <li key={k}>
          <span className={`dot dot-20 ${i.severity === "major" ? "dot-err-soft" : "dot-warn-soft"}`}>
            <Icon name="alert" size={11} strokeWidth={2.8} />
          </span>
          <span className="vstack" style={{ gap: 1, minWidth: 0 }}>
            <span>{i.problem}</span>
            {!compact && i.fix && <span className="faint small">Fix: {i.fix}</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function GoalBar({ score }: { score: number }) {
  return (
    <div className="vstack" style={{ gap: 4 }}>
      <div className="bar" style={{ height: 8 }}>
        <span style={{ width: `${Math.max(2, score)}%`, background: `var(--${scoreTone(score) === "ok" ? "ok" : scoreTone(score) === "warn" ? "warn" : "err"})` }} />
      </div>
      <span className="faint small">
        {score >= 100 ? "Flawless." : score >= 90 ? `${100 - Math.round(score)} points from flawless. Fix the problems below to reach 100.` : "Aim for 90+. Fixing the problems below raises the score."}
      </span>
    </div>
  );
}
