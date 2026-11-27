import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, isBusy, PanelRow, Quality, SceneBoard, StoryboardView } from "../api";
import { Icon } from "../components/icons";
import { GoalBar, IssueList, ScoreHistory, ScoreRing } from "../components/check";
import { ActionButton, CommentAction, Empty, Loading, money, NextBar, OkDot, Pill, Portal, Tabs, Thumb, useToast } from "../components/ui";

const CRITERIA = ["character", "quality", "background", "emotion", "continuity", "framing"] as const;
const LABEL: Record<string, string> = {
  character: "Characters", quality: "Image quality", background: "Place", emotion: "Expressions", continuity: "Continuity", framing: "Framing",
};
const GOOD: Record<string, string> = {
  character: "Characters match their pictures", quality: "Anatomy, hands and props look right", background: "Place and lighting are right", emotion: "Expressions fit the story",
  continuity: "Matches the shots around it", framing: "Camera framing is right",
};
const BAD: Record<string, string> = {
  character: "Characters don’t fully match their pictures", quality: "Anatomy, hands or props have mistakes", background: "Place or lighting is off", emotion: "Expressions don’t fit the story",
  continuity: "Doesn’t match the shots around it", framing: "Camera framing is off",
};

const review = (p: PanelRow) => (p.review && p.review.panel_version === p.version ? p.review : null);
const needsLook = (p: PanelRow) => review(p)?.verdict === "fail" && p.status === "needs_approval";
const ready = (p: PanelRow) => p.status === "approved" || (p.status === "needs_approval" && review(p)?.verdict === "pass");

function shotPill(p: PanelRow) {
  if (p.status === "generating") return <Pill cls="p-info" busy>Drawing</Pill>;
  if (p.status === "in_review") return <Pill cls="p-info" busy>Checking</Pill>;
  if (p.status === "failed") return <Pill cls="p-err">Problem</Pill>;
  if (p.status === "stale") return <Pill cls="p-warn">Out of date</Pill>;
  if (p.status === "approved") return <Pill cls="p-ok" icon="check">Approved</Pill>;
  if (needsLook(p)) return <Pill cls="p-warn">Needs you</Pill>;
  if (p.status === "needs_approval") return <Pill cls="p-ok">Looks good</Pill>;
  return <Pill cls="p-mute">Not drawn</Pill>;
}

export default function StoryboardPage({ pid }: { pid: string }) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["storyboard", pid], queryFn: () => api.storyboard(pid),
    refetchInterval: (s) => (s.state.data?.scenes.some((sc) => sc.panels.some((p) => isBusy(p.status))) ? 2000 : false),
  });
  const plan = useQuery({ queryKey: ["plan", pid], queryFn: () => api.plan(pid), staleTime: 30_000 });
  const names: Record<string, string> = Object.fromEntries((plan.data?.plan.characters ?? []).map((c) => [c.id, c.name]));
  const places: Record<string, string> = Object.fromEntries((plan.data?.plan.locations ?? []).map((l) => [l.id, l.name]));
  const lib = useQuery({ queryKey: ["library", pid], queryFn: () => api.library(pid), staleTime: 30_000 });
  const charImg: Record<string, string | null> = Object.fromEntries((lib.data?.characters ?? []).map((g) => [g.character.id, g.base?.approved_url ?? g.base?.current_url ?? null]));
  const placeImg: Record<string, string | null> = Object.fromEntries((lib.data?.locations ?? []).map((g) => [g.location.id, g.item?.approved_url ?? g.item?.current_url ?? null]));
  const [sceneId, setSceneId] = useState<string | null>(null);
  useEffect(() => {
    if (q.data && !sceneId) setSceneId((q.data.scenes.find((s) => s.board.status !== "approved" && !s.locked_reason) ?? q.data.scenes[0])?.id ?? null);
  }, [q.data, sceneId]);

  if (q.isLoading || !q.data) return <Loading />;
  if (!q.data.scenes.length) return <Empty title="No scenes yet" icon="film">Approve a plan first.</Empty>;
  const scenes = q.data.scenes;
  const scene = scenes.find((s) => s.id === sceneId) ?? scenes[0];
  const idx = scenes.indexOf(scene);
  const refresh = () => ["storyboard", "overview", "finals"].forEach((k) => qc.invalidateQueries({ queryKey: [k, pid] }));

  return (
    <SceneView key={scene.id} pid={pid} scene={scene} index={idx + 1} view={q.data} onChange={refresh} names={names} places={places} charImg={charImg} placeImg={placeImg}
      next={scenes[idx + 1]} onScene={setSceneId}
      tabs={<Tabs label="Scenes" value={scene.id} onChange={setSceneId} items={scenes.map((s, i) => ({
        value: s.id,
        label: <>{s.board.status === "approved" ? <OkDot size={16} /> : s.locked_reason ? <Icon name="clock" size={13} /> : null}Scene {i + 1}</>,
      }))} />} />
  );
}

function SceneView({ pid, scene, index, view, onChange, names, places, charImg, placeImg, next, onScene, tabs }: {
  pid: string; scene: SceneBoard; index: number; view: StoryboardView; onChange: () => void;
  names: Record<string, string>; places: Record<string, string>; charImg: Record<string, string | null>;
  placeImg: Record<string, string | null>; next?: SceneBoard; onScene: (id: string) => void; tabs: React.ReactNode;
}) {
  const toast = useToast();
  const [quality, setQuality] = useState<Quality>(view.draft_quality);
  const [sel, setSelRaw] = useState<string | null>(null);
  const [viewVer, setViewVer] = useState<number | null>(null);
  const setSel = (id: string | null) => { setSelRaw(id); setViewVer(null); };
  const panels = scene.panels;
  const hasAny = panels.some((p) => p.version);
  const todo = panels.filter((p) => !p.version || p.status === "stale" || (p.status === "failed" && !p.version));
  const look = panels.filter(needsLook);
  const busy = panels.filter((p) => isBusy(p.status));
  const nReady = panels.filter(ready).length;
  const current = panels.find((p) => p.shot.id === sel)
    ?? look[0] ?? panels.find((p) => p.version && !ready(p)) ?? panels.find((p) => p.version) ?? panels[0];
  const progress = { done: nReady, total: panels.length, label: "shots ready" };
  // previous / next shot (only shots that have been drawn)
  const drawn = panels.filter((x) => x.version);
  const pos = current ? drawn.findIndex((x) => x.shot.id === current.shot.id) : -1;
  const go = (d: number) => {
    const next = drawn[pos + d];
    if (next) setSel(next.shot.id);
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      if (document.querySelector(".lightbox, .modal-backdrop")) return;  // an open viewer handles its own arrows
      e.preventDefault();
      go(e.key === "ArrowRight" ? 1 : -1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  const previewing = current && viewVer != null && viewVer !== current.version ? current.history.find((h) => h.version === viewVer) ?? null : null;
  const title = `Scene ${index}${scene.title ? ` · ${scene.title}` : ""}`;
  const [boardOpen, setBoardOpen] = useState(false);
  const boardBtn = hasAny && <button className="btn" onClick={() => setBoardOpen(true)}><Icon name="film" size={15} />View board</button>;

  let bar;
  if (scene.locked_reason) {
    const lib = /library|reference|picture/i.test(scene.locked_reason);
    bar = (
      <NextBar step={3} tone="warn" icon="clock" title={`${title} isn’t ready yet`} text={scene.locked_reason}>
        {lib && <Link className="btn btn-primary" to="../library"><Icon name="arrowLeft" size={15} />Characters &amp; places</Link>}
      </NextBar>
    );
  } else if (todo.length && !busy.length) {
    bar = (
      <NextBar step={3} icon="sparkles" progress={hasAny ? progress : undefined}
        title={hasAny ? `Redraw ${todo.length} shot${todo.length > 1 ? "s" : ""} in ${title}` : `Sketch the ${panels.length} shots in ${title}`}
        text={`Quick, low-cost sketches using your approved characters and places. The AI then checks each one against the plan.${view.typical_fix_cost_usd ? ` About ${money(view.typical_fix_cost_usd)} per shot.` : ""}`}>
        <label className="field inline">Quality
          <select value={quality} onChange={(e) => setQuality(e.target.value as Quality)}>
            <option value="low">Sketch (cheapest)</option><option value="medium">Medium</option><option value="high">High</option>
          </select>
        </label>
        <ActionButton kind="primary" icon="sparkles" onClick={async () => {
          const r = await api.generateScene(pid, scene.id, quality); toast.info(`Sketching ${r.queued.length} shot(s)`); onChange();
        }}>{hasAny ? "Redraw" : "Sketch scene"}</ActionButton>
      </NextBar>
    );
  } else if (look.length) {
    bar = (
      <NextBar step={3} tone="warn" icon="eye" progress={progress} title={`${look.length} shot${look.length > 1 ? "s" : ""} need${look.length > 1 ? "" : "s"} your decision`}
        text="The AI flagged a problem. For each flagged shot: fix it with the AI’s suggestion, or approve it anyway if you’re happy." />
    );
  } else if (busy.length) {
    bar = <NextBar step={3} tone="wait" icon="clock" progress={progress} title={`Drawing and checking ${busy.length} shot${busy.length > 1 ? "s" : ""}…`}
      text="Each shot appears below as soon as it’s ready. You can review finished shots meanwhile." />;
  } else if (scene.board.status === "approved") {
    bar = (
      <NextBar step={3} tone="ok" icon="check" progress={progress} title={`${title} is approved`}
        text={next ? "Review the next scene, or start making the final images for this one." : "Every scene is done. Next: make the final images."}>
        {boardBtn}
        {next && !next.locked_reason && next.board.status !== "approved" && <button className="btn" onClick={() => onScene(next.id)}>Next scene<Icon name="arrowRight" size={15} /></button>}
        <Link className="btn btn-primary" to="../finals">Final images<Icon name="arrowRight" size={15} /></Link>
      </NextBar>
    );
  } else if (scene.board.status === "needs_approval") {
    bar = (
      <NextBar step={3} icon="check" progress={progress} title={`Every shot looks good. Approve ${title}`}
        text="Have a quick look at each shot below. Approving locks this scene so its final images can be made.">
        {boardBtn}
        <ActionButton kind="success" icon="check" onClick={async () => { await api.approveBoard(pid, scene.id, []); toast.ok(`Scene ${index} approved`); onChange(); }}>Approve scene</ActionButton>
      </NextBar>
    );
  } else {
    bar = <NextBar step={3} tone="warn" icon="alert" progress={progress} title="Some shots need attention" text="Select the shots marked Problem below and try again." />;
  }

  return (
    <>
      {boardOpen && (
        <BoardModal title={title} scene={scene} names={names} places={places} onClose={() => setBoardOpen(false)}
          onPick={(id) => { setBoardOpen(false); setSel(id); window.scrollTo({ top: 0, behavior: "smooth" }); }} />
      )}
      {bar}
      <div className="row">{tabs}<span className="hstack" style={{ gap: 8 }}><span className="faint small">{title}</span>{boardBtn && scene.board.status !== "approved" && scene.board.status !== "needs_approval" ? boardBtn : null}</span></div>

      {hasAny && (
        <div className="filmstrip" role="list" aria-label={`Shots in ${title}`}>
          {panels.map((p, i) => (
            <button key={p.shot.id} role="listitem" className={`film${current?.shot.id === p.shot.id ? " sel" : ""}`} aria-current={current?.shot.id === p.shot.id}
              onClick={() => setSel(p.shot.id)}>
              <div className="frame">
                {p.url ? <img src={p.url} alt="" style={{ opacity: isBusy(p.status) ? 0.5 : 1 }} />
                  : <div className="thumb-empty">{isBusy(p.status) ? <span className="spinner" /> : <Icon name="image" size={16} />}</div>}
              </div>
              <span className="film-label">
                <span>Shot {i + 1}{review(p) && <span className={`hist-chip hist-${review(p)!.overall >= 90 ? "ok" : review(p)!.overall >= 75 ? "warn" : "err"}`} style={{ marginLeft: 6 }}>{Math.round(review(p)!.overall)}</span>}</span>
                {shotPill(p)}
              </span>
            </button>
          ))}
        </div>
      )}

      {current && current.version ? (
        <div className="split">
          <div className="stack" style={{ gap: 10 }}>
            <div className="viewer">
              <Thumb src={previewing ? previewing.url : current.url}
                alt={`Shot ${panels.indexOf(current) + 1}${previewing ? `, version ${previewing.version}` : ""}: ${current.shot.action}`}
                status={previewing ? undefined : current.status} />
              <div className={`viewer-tag${previewing ? " viewer-tag-old" : ""}`}>
                {previewing ? (
                  <>
                    <Icon name="eye" size={14} />
                    <span>Viewing <b>v{previewing.version}</b> · {Math.round(previewing.overall)}</span>
                    <button className="viewer-tag-btn" onClick={() => setViewVer(null)}>Back to current</button>
                  </>
                ) : current.version ? <span>Current · <b>v{current.version}</b></span> : null}
              </div>
              {drawn.length > 1 && (
                <>
                  <button className="viewer-nav viewer-prev" aria-label="Previous shot" title="Previous shot (←)" disabled={pos <= 0} onClick={() => go(-1)}>
                    <Icon name="arrowLeft" size={20} />
                  </button>
                  <button className="viewer-nav viewer-next" aria-label="Next shot" title="Next shot (→)" disabled={pos >= drawn.length - 1} onClick={() => go(1)}>
                    <Icon name="arrowRight" size={20} />
                  </button>
                  <span className="viewer-count">Shot {pos + 1} of {drawn.length}</span>
                </>
              )}
            </div>
            <div className="vstack" style={{ gap: 6 }}>
              <div className="hstack" style={{ gap: 6 }}>
                <b style={{ fontSize: 15 }}>Shot {panels.indexOf(current) + 1}</b>
                {places[current.shot.location_id] && <span className="chip">{places[current.shot.location_id]}</span>}
                {current.shot.character_ids.map((cid) => {
                  const emo = current.shot.reactions.find((r) => r.character_id === cid)?.emotion;
                  return <span key={cid} className="chip">{names[cid] ?? cid}{emo ? ` · ${emo}` : ""}</span>;
                })}
                {current.shot.framing && <span className="chip">{current.shot.framing}</span>}
              </div>
              <span>{current.shot.action}</span>
              {current.shot.dialogue && <span className="dialogue">“{current.shot.dialogue}”</span>}
            </div>
          </div>
          {previewing
            ? <VersionCard pid={pid} p={current} h={previewing} onView={setViewVer} onChange={() => { setViewVer(null); onChange(); }} />
            : <ReviewCard pid={pid} p={current} cost={view.typical_fix_cost_usd} view={view} onChange={onChange} onView={setViewVer} />}
        </div>
      ) : !hasAny && !scene.locked_reason ? (
        <section className="stack">
          <div className="section-head">
            <h3 className="section-title">What will be sketched</h3>
            <span className="faint small">Each shot uses the approved pictures shown on it, so characters stay on model</span>
          </div>
          <div className="plan-preview">
            {panels.map((p, i) => (
              <article key={p.shot.id} className="card plan-shot">
                <div className="frame">
                  {isBusy(p.status) ? <span className="faint small hstack" style={{ gap: 6 }}><span className="spinner" />Drawing…</span>
                    : <span className="faint small hstack" style={{ gap: 6 }}><Icon name="image" size={15} />{p.shot.framing || "Shot"}</span>}
                  <div className="refs">
                    {p.shot.character_ids.map((cid) => charImg[cid] && <img key={cid} src={charImg[cid]!} alt={names[cid] ?? cid} title={names[cid] ?? cid} />)}
                    {placeImg[p.shot.location_id] && <img src={placeImg[p.shot.location_id]!} alt={places[p.shot.location_id]} title={places[p.shot.location_id]} style={{ width: 44 }} />}
                  </div>
                </div>
                <div className="place-body">
                  <div className="row" style={{ gap: 6 }}><b>Shot {i + 1}</b><span className="faint small">{places[p.shot.location_id] ?? ""}</span></div>
                  <span className="small clamp-2">{p.shot.action}</span>
                  {p.shot.dialogue && <span className="dialogue small clamp-2">“{p.shot.dialogue}”</span>}
                </div>
              </article>
            ))}
          </div>
        </section>
      ) : null}
    </>
  );
}

function ReviewCard({ pid, p, cost, view, onChange, onView }: {
  pid: string; p: PanelRow; cost: number | null; view: StoryboardView; onChange: () => void; onView: (v: number | null) => void;
}) {
  const toast = useToast();
  const [scores, setScores] = useState(false);
  const r = review(p);
  const busy = isBusy(p.status);
  const issues = r?.issues ?? [];
  const crit = r ? CRITERIA.filter((c) => c in r.scores) : [];
  const bad = r ? crit.filter((c) => (r.scores[c] ?? 0) < 7) : [];
  const oldCheck = !!r && !("quality" in r.scores);
  const best = p.history.length ? p.history.reduce((a, b) => (b.overall >= a.overall ? b : a)) : null;
  // "Fix with AI" edits the best version using THAT version's own problems (computed by the server too).
  const baseFrom = best && r && best.overall > r.overall ? best.version : p.version;
  const baseReview = (baseFrom !== p.version ? p.history.find((h) => h.version === baseFrom)?.review : r) ?? r;
  const baseIssues = baseReview?.issues ?? [];
  const majors = baseIssues.filter((i) => i.severity === "major");
  const targets = majors.length ? majors : baseIssues.slice(0, 2);
  const detail = targets.some((i) => ["anatomy", "object", "character"].includes(i.category ?? ""));
  const smallOnly = !majors.length;
  const fixLabel = !targets.length ? "Improve with AI"
    : majors.length ? `Fix ${majors.length} serious problem${majors.length > 1 ? "s" : ""}`
    : `Try to fix ${targets.length} small detail${targets.length > 1 ? "s" : ""}`;
  const noise = !!best && !!r && best.version !== p.version && Math.abs(best.overall - r.overall) <= 2;
  // three or more versions after the best one, none of them better: stop spending
  const stuck = !!best && p.history.filter((h) => h.version > best.version).length >= 2;
  const fix = async (comment: string, msg = "Redrawing this shot") => { await api.fixPanel(pid, p.shot.id, comment); toast.info(msg); onChange(); };
  const canFix = !busy && !!p.version && p.status !== "failed";
  const headline = !r ? (busy ? (p.status === "in_review" ? "AI is checking…" : "Drawing this shot…") : "Not checked yet")
    : r.overall >= 100 ? "Flawless" : r.verdict === "fail" ? "Needs a fix" : issues.length || bad.length ? "Good, can be better" : "Looks great";
  const costTxt = detail ? " · medium quality" : cost ? ` · about ${money(cost)}` : "";
  const improve = async () => {
    const res = await api.improvePanel(pid, p.shot.id);
    toast.info(`Fixing ${res.fixing.length} problem${res.fixing.length === 1 ? "" : "s"} on v${res.base_version}`);
    onChange();
  };

  return (
    <aside className="card review" aria-label="AI check for this shot">
      <div className="verdict">
        <ScoreRing score={r ? r.overall : null} busy={busy && !r} />
        <div className="vstack grow1" style={{ gap: 2 }}>
          <span className="faint small" style={{ fontWeight: 600 }}>AI check{r ? ` · version ${p.version}` : ""}</span>
          <b style={{ fontSize: 16 }}>{headline}</b>
        </div>
      </div>
      {r && <GoalBar score={r.overall} />}
      <ScoreHistory items={p.history} current={p.version} selected={p.version} onSelect={(v) => onView(v === p.version ? null : v)} />
      {p.history.length > 1 && <span className="faint small" style={{ marginTop: -6 }}>Click a version to see it.</span>}

      {p.status === "failed" && <span className="err-text small">{p.error || "Something went wrong with this shot."}</span>}
      {p.status === "stale" && <span className="warn-text small">{p.stale_reason || "A character or place changed"}. Redraw this shot.</span>}

      {r && issues.length > 0 && (
        <div className="issue-area">
          <b className="small">{issues.length} problem{issues.length > 1 ? "s" : ""} found</b>
          <IssueList issues={issues} />
        </div>
      )}
      {r && issues.length === 0 && (
        <ul className="check-list">
          {crit.map((c) => {
            const ok = (r.scores[c] ?? 0) >= 7;
            return (
              <li key={c}>
                <span className={`dot dot-20 ${ok ? "dot-ok-soft" : "dot-warn-soft"}`}><Icon name={ok ? "check" : "alert"} size={11} strokeWidth={2.8} /></span>
                <span style={{ color: ok ? undefined : "var(--warn-ink)" }}>{ok ? GOOD[c] : BAD[c]}</span>
              </li>
            );
          })}
        </ul>
      )}
      {r && scores && (
        <div className="stack" style={{ gap: 5 }}>
          {crit.map((c) => (
            <div key={c} className="score-row" title={`weight ${view.criteria_weights[c] ?? 0}`}>
              <span>{LABEL[c]}</span>
              <span className={`bar ${(r.scores[c] ?? 0) < 7 ? "bar-warn" : "bar-ok"}`}><span style={{ width: `${(r.scores[c] ?? 0) * 10}%` }} /></span>
              <span className="tnum">{r.scores[c]}</span>
            </div>
          ))}
          {r.notes && <span className="faint small">{r.notes}</span>}
        </div>
      )}

      {/* the best version so far is not the current one: go back to it for free */}
      {best && r && best.version !== p.version && best.overall > r.overall && !busy && (
        <div className="check-box">
          <span className="small"><b>v{best.version} scored {Math.round(best.overall)}</b>, the best so far. Later redraws scored lower.</span>
          <ActionButton kind="soft" size="sm" icon="refresh"
            onClick={async () => { await api.restorePanel(pid, p.shot.id, best.version); toast.ok(`Back to version ${best.version}`); onChange(); }}>
            Use v{best.version} · free
          </ActionButton>
        </div>
      )}
      {stuck && r?.verdict === "pass" && <span className="warn-text small">More redraws aren’t raising the score. Approving now saves money.</span>}

      {/* main actions: approve first when it already passes */}
      {p.status === "needs_approval" && r?.verdict === "pass" && (
        <ActionButton kind="success" block icon="check"
          onClick={async () => { await api.acceptPanel(pid, p.shot.id); toast.ok("Shot approved"); onChange(); }}>Approve this shot</ActionButton>
      )}
      {r && canFix && targets.length > 0 && r.overall < 100 && !(smallOnly && r.verdict === "pass") && (
        <>
          <ActionButton kind="primary" block icon="sparkles" title={targets.map((t) => t.fix).join(" ")} onClick={improve}>
            {fixLabel}{costTxt}
          </ActionButton>
          <span className="faint small" style={{ textAlign: "center", marginTop: -6 }}>
            Edits v{baseFrom}{baseFrom !== p.version ? " (the best version)" : ""} and fixes only {targets.length > 1 ? "these" : "this"}:
            {" "}{targets.map((t) => t.problem.split(/[,.;(]/)[0]).join("; ")}.
          </span>
        </>
      )}
      {r && canFix && targets.length > 0 && smallOnly && r.verdict === "pass" && r.overall < 100 && (
        <div className="check-box small">
          <span>Only small details are left. Redrawing for them usually doesn’t raise the score, because each redraw re-renders the whole frame.</span>
          <ActionButton kind="text" size="sm" icon="sparkles" onClick={improve}>{fixLabel} anyway{costTxt}</ActionButton>
        </div>
      )}
      {noise && <span className="faint small">A 1–2 point difference between versions is normal variation in the AI check.</span>}
      {p.status === "needs_approval" && r?.verdict !== "pass" && (
        <ActionButton kind="secondary" block icon="check"
          onClick={async () => { await api.acceptPanel(pid, p.shot.id); toast.ok("Shot approved"); onChange(); }}>Approve anyway</ActionButton>
      )}
      {p.status === "approved" && <Pill cls="p-ok" icon="check">You approved this shot</Pill>}
      {p.status === "failed" && p.version && !r && (
        <ActionButton kind="primary" block icon="refresh" onClick={async () => { await api.reviewPanel(pid, p.shot.id); onChange(); }}>Run the AI check again</ActionButton>
      )}
      {canFix && (
        <CommentAction block kind="secondary" icon="edit" label="Describe your own fix" submitLabel="Redraw"
          placeholder="e.g. one pebble only, in his right hand" onSubmit={(c) => fix(c)} />
      )}
      {oldCheck && !busy && (
        <div className="check-box small">
          <span>This shot was checked before the stricter check (anatomy, hands, duplicated props) was added.</span>
          <ActionButton kind="soft" size="sm" icon="eye" onClick={async () => { await api.reviewPanel(pid, p.shot.id); toast.info("Re-checking this shot"); onChange(); }}>Re-check with the strict AI check</ActionButton>
        </div>
      )}
      <div className="link-row">
        {canFix && <ActionButton kind="text" size="sm" icon="refresh" onClick={() => fix("Make a fresh version of this shot that follows the plan closely, with correct anatomy and a single copy of each prop.")}>New version</ActionButton>}
        {r && <button className="btn btn-text btn-sm" onClick={() => setScores(!scores)}>{scores ? "Hide scores" : "Show scores"}</button>}
      </div>
      {canFix && <span className="faint small" style={{ textAlign: "center" }}>Every fix redraws only this shot and checks it again.</span>}
    </aside>
  );
}

function VersionCard({ pid, p, h, onView, onChange }: {
  pid: string; p: PanelRow; h: PanelRow["history"][number]; onView: (v: number | null) => void; onChange: () => void;
}) {
  const toast = useToast();
  const r = h.review;
  const busy = isBusy(p.status);
  const delta = p.history.find((x) => x.version === p.version);
  return (
    <aside className="card review" aria-label={`Version ${h.version}`}>
      <div className="verdict">
        <ScoreRing score={h.overall} />
        <div className="vstack grow1" style={{ gap: 2 }}>
          <span className="faint small" style={{ fontWeight: 600 }}>AI check · version {h.version} (earlier)</span>
          <b style={{ fontSize: 16 }}>{h.overall >= 100 ? "Flawless" : h.verdict === "fail" ? "Needs a fix" : r.issues.length ? "Good, can be better" : "Looks great"}</b>
        </div>
      </div>
      <GoalBar score={h.overall} />
      <ScoreHistory items={p.history} current={p.version} selected={h.version} onSelect={(v) => onView(v === p.version ? null : v)} />
      <span className="faint small" style={{ marginTop: -6 }}>Click a version to see it.</span>
      <div className="issue-area">
        {h.comment && <span className="small"><span className="faint">Made with:</span> “{h.comment}”</span>}
        {r.issues.length > 0
          ? <><b className="small">{r.issues.length} problem{r.issues.length > 1 ? "s" : ""} in this version</b><IssueList issues={r.issues} /></>
          : <span className="small" style={{ color: "var(--ok-ink)" }}>No problems found in this version.</span>}
      </div>
      <ActionButton kind={delta && h.overall > delta.overall ? "primary" : "secondary"} block icon="refresh" disabled={busy}
        onClick={async () => { await api.restorePanel(pid, p.shot.id, h.version); toast.ok(`Version ${h.version} is now current`); onChange(); }}>
        Use version {h.version} · free
      </ActionButton>
      <button className="btn btn-block" onClick={() => onView(null)}>Back to current (v{p.version})</button>
      <span className="faint small" style={{ textAlign: "center" }}>Using an earlier version costs nothing; its AI check is kept.</span>
    </aside>
  );
}

function BoardModal({ title, scene, names, places, onClose, onPick }: {
  title: string; scene: SceneBoard; names: Record<string, string>; places: Record<string, string>;
  onClose: () => void; onPick: (shotId: string) => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // The composed board (panels + captions drawn by the app) is the storyboard: show it full screen.
  if (scene.board.url) {
    return (
      <Portal>
        <div className="lightbox board-view" onClick={onClose} role="dialog" aria-modal aria-label={`${title} storyboard`}>
          <div className="board-toolbar" onClick={(e) => e.stopPropagation()}>
            <span className="board-toolbar-title">{title} · storyboard{scene.board.status === "approved" ? " · approved" : ""}</span>
            <a className="btn btn-sm btn-primary" href={scene.board.url} download={`${scene.id}_storyboard.png`}><Icon name="download" size={14} />Download PNG</a>
            <button className="lb-btn" style={{ position: "static", width: 34, height: 34 }} aria-label="Close" onClick={onClose}><Icon name="x" size={18} /></button>
          </div>
          <img src={scene.board.url} alt={`${title} storyboard`} onClick={(e) => e.stopPropagation()} />
        </div>
      </Portal>
    );
  }
  const panels = scene.panels;
  const ready = panels.filter((p) => p.url).length;
  return (
    <Portal>
      <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
        <div className="modal board-modal" role="dialog" aria-modal aria-label={`${title} storyboard`}>
          <div className="card-head">
            <div className="vstack" style={{ gap: 0 }}>
              <b style={{ fontSize: 16 }}>{title}</b>
              <span className="faint small">Storyboard · {panels.length} shots{ready < panels.length ? ` · ${ready} drawn` : ""} · {scene.board.status === "approved" ? "approved" : "not approved yet"}</span>
            </div>
            <div className="hstack" style={{ gap: 6 }}>
              <span className="faint small">The board image is ready once every shot is drawn.</span>
              <button className="btn btn-text btn-sm btn-icon" aria-label="Close" onClick={onClose}><Icon name="x" size={16} /></button>
            </div>
          </div>
          <div className="board-grid">
            {panels.map((p, i) => {
              const rv = review(p);
              return (
                <button key={p.shot.id} className="board-cell" onClick={() => onPick(p.shot.id)} title="Review this shot">
                  <div className="board-frame">
                    {p.url ? <img src={p.url} alt={`${p.shot.id}: ${p.shot.action}`} />
                      : <div className="thumb-empty">{isBusy(p.status) ? <span className="spinner" /> : <Icon name="image" size={18} />}Not drawn yet</div>}
                    <span className="board-num">{i + 1}</span>
                  </div>
                  <div className="board-caption">
                    <div className="row" style={{ gap: 6 }}>
                      <b className="small">{p.shot.id}</b>
                      <span className="hstack" style={{ gap: 4 }}>
                        {rv && <span className={`hist-chip hist-${rv.overall >= 90 ? "ok" : rv.overall >= 75 ? "warn" : "err"}`}>{Math.round(rv.overall)}</span>}
                        {shotPill(p)}
                      </span>
                    </div>
                    <span className="faint small">{[p.shot.framing, places[p.shot.location_id]].filter(Boolean).join(" · ")}</span>
                    <span className="small clamp-2">{p.shot.action}</span>
                    {p.shot.dialogue && <span className="dialogue small clamp-2">“{p.shot.dialogue}”</span>}
                    {p.shot.character_ids.length > 0 && (
                      <span className="faint small">{p.shot.character_ids.map((c) => names[c] ?? c).join(", ")}</span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>
          <div className="modal-foot" style={{ justifyContent: "space-between" }}>
            <span className="faint small">Captions are added by the app, not drawn by the AI. Click a shot to review it.</span>
            <button className="btn" onClick={onClose}>Close</button>
          </div>
        </div>
      </div>
    </Portal>
  );
}
