import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, Overview } from "../api";
import { useCreateProject } from "../App";
import { Icon, IconName } from "../components/icons";
import { TAGLINE, Wordmark } from "../components/Logo";
import { Loading, useToast } from "../components/ui";
import { progress } from "../progress";

const HOW: { icon: IconName; title: string; text: string }[] = [
  { icon: "upload", title: "1. Upload the plan", text: "We read every character, place and shot." },
  { icon: "user", title: "2. Approve the looks", text: "One picture per character and place." },
  { icon: "film", title: "3. Review the storyboard", text: "AI sketches each shot and flags problems." },
  { icon: "image", title: "4. Get final images", text: "A finished 16:9 image for every shot." },
];

export default function HomePage() {
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects, refetchInterval: 10_000 });
  const list = projects.data ?? [];

  return (
    <div className="content" style={{ maxWidth: 1080 }}>
      <div className="vstack" style={{ gap: 2 }}>
        <h1 style={{ fontSize: 22, fontWeight: 700 }}>{list.length ? "Welcome back" : <>Welcome to <Wordmark size={22} /></>}</h1>
        <p className="page-intro"><b style={{ color: "var(--text)" }}>{TAGLINE}</b> Turn an approved Shot Planner into consistent storyboards and final images, with every cost logged.</p>
      </div>

      <section className="card card-pad stack">
        <h2 className="section-title">Start a new storyboard</h2>
        <DropZone />
        <ol className="how">
          {HOW.map((h) => (
            <li key={h.title}><span className="icon-tile" style={{ width: 30, height: 30, borderRadius: 8 }}><Icon name={h.icon} size={15} /></span>
              <span><b>{h.title}</b><span className="faint">{h.text}</span></span></li>
          ))}
        </ol>
      </section>

      {projects.isLoading ? <Loading /> : list.length > 0 && (
        <section className="stack">
          <h2 className="section-title">Your projects</h2>
          <div className="grid g-projects">{list.map((o) => <ProjectCard key={o.project.id} o={o} />)}</div>
        </section>
      )}
    </div>
  );
}

function ProjectCard({ o }: { o: Overview }) {
  const p = progress(o);
  return (
    <Link to={`/p/${o.project.id}/${p.step.key}`} className="card place-card" style={{ textDecoration: "none", color: "var(--text)" }}>
      <div className="thumb" style={{ aspectRatio: "16 / 9" }}>
        {o.cover_url ? <img src={o.cover_url} alt="" style={{ cursor: "pointer" }} /> : <div className="thumb-empty"><Icon name="film" size={22} strokeWidth={1.5} /></div>}
      </div>
      <div className="place-body">
        <b>{o.project.title}</b>
        <span className="faint small">Step {p.step.n} of 5 · {p.step.name}</span>
        <div className="bar"><span style={{ width: `${Math.max(p.pct, 3)}%` }} /></div>
        <span className="small" style={{ color: "var(--primary)", fontWeight: 600 }}>{p.cta} →</span>
      </div>
    </Link>
  );
}

function DropZone() {
  const create = useCreateProject();
  const toast = useToast();
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const pick = async (files: FileList | null) => {
    const f = files?.[0];
    if (!f) return;
    setBusy(true);
    try { await create(f); } catch (e) { toast.error(e); } finally { setBusy(false); }
  };
  return (
    <label className={`dropzone${drag ? " active" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
      onDrop={(e) => { e.preventDefault(); setDrag(false); pick(e.dataTransfer.files); }}>
      <span className="icon-tile" style={{ background: "var(--primary)", color: "#fff" }}>{busy ? <span className="spinner" /> : <Icon name="upload" size={18} />}</span>
      <span className="vstack" style={{ gap: 0 }}>
        <b>{busy ? "Uploading…" : "Drop your Shot Planner PDF here"}</b>
        <span className="faint small">or <span style={{ color: "var(--primary)", fontWeight: 600 }}>browse files</span> · PDF up to 30 MB</span>
      </span>
      <input type="file" accept="application/pdf" className="sr-only" aria-label="Upload Shot Planner PDF"
        onChange={(e) => { pick(e.target.files); e.target.value = ""; }} />
    </label>
  );
}
