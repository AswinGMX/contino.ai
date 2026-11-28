import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, User } from "./api";
import { Icon } from "./components/icons";
import { Logo } from "./components/Logo";
import { ActionButton, Avatar, BudgetCard, Pill, Portal, UploadButton, useToast } from "./components/ui";
import FinalsPage from "./pages/FinalsPage";
import HomePage from "./pages/HomePage";
import LibraryPage from "./pages/LibraryPage";
import PlanPage from "./pages/PlanPage";
import StoryboardPage from "./pages/StoryboardPage";
import UsagePage from "./pages/UsagePage";
import { steps } from "./progress";

export default function App() {
  const me = useQuery({ queryKey: ["me"], queryFn: api.me, retry: false, staleTime: 60_000 });
  if (me.isLoading) return <div className="login"><span className="spinner" /></div>;
  if (me.error instanceof ApiError && me.error.status === 401) return <Login />;
  if (me.error) {
    return <div className="login"><div className="card card-pad stack" style={{ width: "min(420px, 100%)" }}>
      <b>Can’t reach the server</b><span className="muted small">{(me.error as Error).message}</span>
      <button className="btn" onClick={() => me.refetch()}>Try again</button></div></div>;
  }
  return (
    <div className="app">
      <Sidebar user={me.data!.user} />
      <div className="main">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/p/:pid/*" element={<ProjectShell />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </div>
    </div>
  );
}

export function useCreateProject() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  return async (f: File) => {
    const p = await api.createProject(f);
    qc.invalidateQueries({ queryKey: ["projects"] });
    toast.ok(`“${p.title}” uploaded. Reading the shots…`);
    nav(`/p/${p.id}/plan`);
  };
}

function Sidebar({ user }: { user: User }) {
  const budget = useQuery({ queryKey: ["budget"], queryFn: api.budget, refetchInterval: 15_000 });
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects, refetchInterval: 20_000 });
  const create = useCreateProject();
  const loc = useLocation();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const [toDelete, setToDelete] = useState<DeleteTarget | null>(null);
  return (
    <aside className="sidebar">
      <Link to="/" className="brand" aria-label="contino.ai home"><Logo size={32} tagline /></Link>
      <div className="sidebar-new">
        <UploadButton label="New project" icon="plus" kind="primary" size={undefined} accept="application/pdf" onFile={create} />
      </div>
      <nav className="nav" aria-label="Main">
        <NavLink to="/" end className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
          <Icon name="home" size={19} strokeWidth={1.75} /><span>Home</span>
        </NavLink>
        {!!projects.data?.length && <div className="nav-label">Projects</div>}
        {projects.data?.map((o) => (
          <div key={o.project.id} className={`nav-row${loc.pathname.startsWith(`/p/${o.project.id}/`) ? " active" : ""}`}>
            <Link to={`/p/${o.project.id}/plan`} className={`nav-item${loc.pathname.startsWith(`/p/${o.project.id}/`) ? " active" : ""}`}
              title={o.project.title}>
              <Icon name="folder" size={18} strokeWidth={1.75} /><span>{o.project.title}</span>
            </Link>
            <button className="nav-del" aria-label={`Delete ${o.project.title}`} title="Delete project"
              onClick={() => setToDelete({ id: o.project.id, title: o.project.title, shots: o.counts.shots, jobs: o.active_jobs })}>
              <Icon name="trash" size={15} />
            </button>
          </div>
        ))}
      </nav>
      <div className="sidebar-foot stack" style={{ gap: 10 }}>
        <BudgetCard budget={budget.data} />
        <div className="user-chip">
          <Avatar name={user.name || user.email} size={32} />
          <span className="vstack grow1" style={{ gap: 0, minWidth: 0 }}>
            <b className="small" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{user.name || user.email.split("@")[0]}</b>
            <span className="faint small" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{user.email}</span>
          </span>
          <button className="btn btn-text btn-sm btn-icon" title="Sign out" aria-label="Sign out"
            onClick={async () => { await api.logout(); window.location.assign("/"); }}>
            <Icon name="logout" size={16} />
          </button>
        </div>
      </div>
      {toDelete && <DeleteDialog target={toDelete} onClose={() => setToDelete(null)} onDeleted={async () => {
        const wasOpen = loc.pathname.startsWith(`/p/${toDelete.id}/`);
        setToDelete(null);
        await qc.invalidateQueries({ queryKey: ["projects"] });
        qc.removeQueries({ queryKey: ["overview", toDelete.id] });
        toast.ok(`“${toDelete.title}” deleted`);
        if (wasOpen) nav("/");
      }} />}
    </aside>
  );
}

function ProjectShell() {
  const { pid = "" } = useParams();
  const loc = useLocation();
  const ov = useQuery({
    queryKey: ["overview", pid], queryFn: () => api.overview(pid),
    refetchInterval: (q) => (q.state.data?.active_jobs ? 2000 : 8000),
  });
  const o = ov.data;
  if (ov.error instanceof ApiError && ov.error.status === 404) {
    return <div className="content"><div className="empty"><h3>Project not found</h3><Link to="/">Back to home</Link></div></div>;
  }
  const st = steps(o);
  const here = loc.pathname.split("/")[3] ?? "plan";

  return (
    <>
      <header className="proj-header">
        <div className="proj-header-inner">
          <div className="row">
            <div className="vstack" style={{ gap: 2 }}>
              <Link to="/" className="back-link"><Icon name="arrowLeft" size={13} />All projects</Link>
              <h1 className="proj-title">{o?.project.title ?? "Loading…"}</h1>
            </div>
            {o && o.active_jobs > 0 && <Pill cls="p-info" busy>Working on {o.active_jobs} item{o.active_jobs > 1 ? "s" : ""}…</Pill>}
          </div>
          <ol className="stepper" aria-label="Project progress">
            {st.map((s, i) => (
              <li key={s.key}>
                <Link to={s.key} className={`step-link${here === s.key ? " current" : ""}`} aria-current={here === s.key ? "step" : undefined}>
                  {s.done
                    ? <span className="dot dot-26 dot-done"><Icon name="check" size={14} strokeWidth={3} /></span>
                    : <span className={`dot dot-26 ${here === s.key ? "dot-current" : "dot-todo"}`}>{s.n}</span>}
                  <span className="vstack" style={{ gap: 0, lineHeight: 1.25, minWidth: 0 }}>
                    <span className="step-name">{s.name}</span><span className="step-sub">{s.sub}</span>
                  </span>
                </Link>
                {i < st.length - 1 && <span className={`step-line${s.done ? " done" : ""}`} aria-hidden />}
              </li>
            ))}
          </ol>
        </div>
      </header>
      <div className="content">
        <Routes>
          <Route index element={<Navigate to="plan" replace />} />
          <Route path="plan" element={<PlanPage pid={pid} />} />
          <Route path="library" element={<LibraryPage pid={pid} />} />
          <Route path="storyboard" element={<StoryboardPage pid={pid} />} />
          <Route path="finals" element={<FinalsPage pid={pid} />} />
          <Route path="usage" element={<UsagePage pid={pid} />} />
        </Routes>
      </div>
    </>
  );
}

function Login() {
  const cfg = useQuery({ queryKey: ["config"], queryFn: api.config, retry: false });
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const qc = useQueryClient();
  const needCode = !!cfg.data?.access_code_required;
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const res = await api.login(email, name, code);
      // Drop anything cached from a previous account, then mark this user as signed in.
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== "me" });
      qc.setQueryData(["me"], { user: res.user });
    } catch (x) { setErr(x instanceof Error ? x.message : String(x)); } finally { setBusy(false); }
  };
  return (
    <div className="login">
      <form className="card card-pad stack" style={{ width: "min(420px, 100%)", gap: 14 }} onSubmit={submit}>
        <Logo size={40} tagline />
        <div className="vstack" style={{ gap: 2 }}>
          <b style={{ fontSize: 17 }}>Sign in</b>
          <span className="muted small">Your email is your account. Your projects are private to you.</span>
        </div>
        <label className="field">Email<input type="email" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com" /></label>
        <label className="field">Your name <span className="faint" style={{ fontWeight: 400 }}>(optional)</span>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Aswin" /></label>
        {needCode && (
          <label className="field">Team access code<input type="password" required value={code} onChange={(e) => setCode(e.target.value)} /></label>
        )}
        {err && <span className="err-text small">{err}</span>}
        <button className="btn btn-primary" type="submit" disabled={busy || !email}>
          {busy && <span className="spinner" />}Continue
        </button>
      </form>
    </div>
  );
}

type DeleteTarget = { id: string; title: string; shots: number; jobs: number };

function DeleteDialog({ target, onClose, onDeleted }: { target: DeleteTarget; onClose: () => void; onDeleted: () => Promise<void> }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <Portal>
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="alertdialog" aria-modal aria-label="Delete project" style={{ width: "min(440px, 100%)" }}>
        <div className="card-pad stack" style={{ gap: 10 }}>
          <span className="icon-tile" style={{ background: "var(--err-soft)", color: "var(--err)" }}><Icon name="trash" size={18} /></span>
          <b style={{ fontSize: 16 }}>Delete “{target.title}”?</b>
          <p className="muted small">
            This permanently removes the plan, every character and place picture, the storyboards and final images
            {target.shots ? ` for all ${target.shots} shots` : ""}. It can’t be undone.
          </p>
          {target.jobs > 0 && <p className="warn-text small">{target.jobs} item{target.jobs > 1 ? "s are" : " is"} still being made and will be stopped.</p>}
          <p className="faint small">Money already spent stays counted in the budget. Anything you exported to the output/ folder is kept.</p>
        </div>
        <div className="modal-foot">
          <button className="btn" onClick={onClose} autoFocus>Cancel</button>
          <ActionButton kind="danger" icon="trash" onClick={async () => { await api.deleteProject(target.id); await onDeleted(); }}>Delete project</ActionButton>
        </div>
      </div>
    </div>
    </Portal>
  );
}
