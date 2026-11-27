import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, FinalRow, FinalsView, isBusy, Quality } from "../api";
import { Icon } from "../components/icons";
import { ActionButton, CommentAction, Empty, Loading, money, NextBar, OkDot, Pill, Tabs, Thumb, useToast } from "../components/ui";

type Filter = "all" | "ready" | "attention";
type Scene = FinalsView["scenes"][number];

const isReady = (f: FinalRow) => f.status === "approved";

function finalPill(f: FinalRow) {
  switch (f.status) {
    case "approved": return <Pill cls="p-ok" icon="check">Approved</Pill>;
    case "needs_approval": return <Pill cls="p-warn">Check &amp; approve</Pill>;
    case "generating": case "rejected": return <Pill cls="p-info" busy>Making…</Pill>;
    case "stale": return <Pill cls="p-warn">Out of date</Pill>;
    case "failed": return <Pill cls="p-err">Failed</Pill>;
    default: return <Pill cls="p-mute">Not made yet</Pill>;
  }
}

export default function FinalsPage({ pid }: { pid: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [filter, setFilter] = useState<Filter>("all");
  const [quality, setQuality] = useState<Quality | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const q = useQuery({
    queryKey: ["finals", pid], queryFn: () => api.finals(pid),
    refetchInterval: (s) => (s.state.data?.scenes.some((sc) => sc.shots.some((f) => isBusy(f.status))) ? 2000 : false),
  });
  if (q.isLoading || !q.data) return <Loading />;
  const d = q.data;
  const qual = quality ?? d.final_quality;
  const refresh = () => ["finals", "overview"].forEach((k) => qc.invalidateQueries({ queryKey: [k, pid] }));
  const all = d.scenes.flatMap((sc) => sc.shots.map((f) => ({ sc, f })));
  const readyN = all.filter((x) => isReady(x.f)).length;
  const make = all.filter((x) => x.sc.board_approved && ["pending", "stale", "failed"].includes(x.f.status));
  const waiting = all.filter((x) => x.f.status === "needs_approval");
  const busy = all.filter((x) => isBusy(x.f.status));
  const unlocked = d.scenes.some((s) => s.board_approved);
  const progress = { done: readyN, total: all.length, label: "approved" };
  const qualityPicker = (
    <label className="field inline">Quality
      <select value={qual} onChange={(e) => setQuality(e.target.value as Quality)}>
        <option value="high">Best</option><option value="medium">Medium</option><option value="low">Draft</option>
      </select>
    </label>
  );

  const makeAll = async () => {
    let n = 0;
    for (const sc of d.scenes.filter((s) => s.board_approved)) {
      const ids = sc.shots.filter((f) => f.status === "pending" || f.status === "stale").map((f) => f.shot.id);
      if (ids.length) n += (await api.renderFinals(pid, sc.id, qual, ids)).queued.length;
      for (const f of sc.shots.filter((f) => f.status === "failed")) { await api.retryFinal(pid, f.shot.id); n++; }
    }
    toast.info(`Making ${n} image${n === 1 ? "" : "s"}`);
    refresh();
  };

  let bar;
  if (!unlocked) {
    bar = (
      <NextBar step={4} tone="warn" icon="film" title="Approve a storyboard scene first" text="Final images are made from approved storyboard sketches.">
        <Link className="btn btn-primary" to="../storyboard"><Icon name="arrowLeft" size={15} />Storyboard</Link>
      </NextBar>
    );
  } else if (make.length) {
    bar = (
      <NextBar step={4} icon="sparkles" progress={progress} title={`Make ${make.length} final image${make.length > 1 ? "s" : ""}`}
        text={`High-quality 16:9 images from your approved sketches, with the same characters and places.${d.typical_final_cost_usd ? ` About ${money(d.typical_final_cost_usd * make.length)} in total.` : ""}`}>
        {qualityPicker}
        <ActionButton kind="primary" icon="sparkles" onClick={makeAll}>Make {make.length} image{make.length > 1 ? "s" : ""}</ActionButton>
      </NextBar>
    );
  } else if (waiting.length) {
    bar = <NextBar step={4} tone="warn" icon="eye" progress={progress} title={`Check ${waiting.length} image${waiting.length > 1 ? "s" : ""} and approve them`}
      text="Approve each image below, or click Change and describe the fix in one line." />;
  } else if (busy.length) {
    bar = <NextBar step={4} tone="wait" icon="clock" progress={progress} title={`Making ${busy.length} image${busy.length > 1 ? "s" : ""}…`}
      text="Each image appears below as soon as it’s ready." />;
  } else if (readyN === all.length) {
    bar = (
      <NextBar step={4} tone="ok" icon="check" progress={progress} title="Every final image is approved" text="Download your images and the cost log.">
        <a className="btn" href={api.exportZipUrl(pid)}><Icon name="download" size={15} />Download images</a>
        <Link className="btn btn-primary" to="../usage">Next: cost<Icon name="arrowRight" size={15} /></Link>
      </NextBar>
    );
  } else {
    bar = (
      <NextBar step={4} tone="warn" icon="film" progress={progress} title="Approve the remaining storyboard scenes" text="Their final images unlock once each scene is approved.">
        <Link className="btn btn-primary" to="../storyboard">Storyboard<Icon name="arrowRight" size={15} /></Link>
      </NextBar>
    );
  }

  return (
    <>
      {bar}
      <div className="row">
        <Tabs label="Filter images" value={filter} onChange={setFilter} items={[
          { value: "all", label: "All", n: all.length },
          { value: "ready", label: "Approved", n: readyN },
          { value: "attention", label: "To do", n: all.length - readyN },
        ]} />
        {readyN > 0 && <a className="btn btn-sm" href={api.exportZipUrl(pid)}><Icon name="download" size={14} />Download approved ({readyN})</a>}
      </div>

      {d.scenes.length === 0 && <Empty title="No scenes yet" />}
      {d.scenes.map((sc, si) => {
        const shots = sc.shots.map((f, i) => ({ f, n: i + 1 }))
          .filter(({ f }) => filter === "all" || (filter === "ready" ? isReady(f) : !isReady(f)));
        if (!shots.length) return null;
        const r = sc.shots.filter(isReady).length;
        const done = r === sc.shots.length;
        const expanded = open[sc.id] ?? !done;
        return (
          <section key={sc.id} className="card">
            <button className="scene-toggle" style={{ borderTop: 0 }} aria-expanded={expanded} onClick={() => setOpen((o) => ({ ...o, [sc.id]: !expanded }))}>
              <span className="chev" style={{ transform: expanded ? "none" : "rotate(-90deg)" }}><Icon name="chevronDown" size={16} /></span>
              {done && <OkDot size={18} />}
              <b>Scene {si + 1}{sc.title ? ` · ${sc.title}` : ""}</b>
              <span className="faint small" style={{ marginLeft: "auto" }}>{sc.board_approved ? `${r} of ${sc.shots.length} approved` : "Storyboard not approved yet"}</span>
            </button>
            {expanded && (
              <div style={{ padding: "0 16px 16px" }}>
                {!sc.board_approved ? (
                  <p className="faint small">Approve this scene in the <Link to="../storyboard">storyboard</Link> to make its final images.</p>
                ) : (
                  <div className="grid g-finals">
                    {shots.map(({ f, n }) => <FinalCard key={f.shot.id} pid={pid} sc={sc} f={f} n={n} qual={qual} onChange={refresh} />)}
                  </div>
                )}
              </div>
            )}
          </section>
        );
      })}
    </>
  );
}

function FinalCard({ pid, sc, f, n, qual, onChange }: { pid: string; sc: Scene; f: FinalRow; n: number; qual: Quality; onChange: () => void }) {
  const toast = useToast();
  return (
    <article className="card place-card" style={{ boxShadow: "none" }}>
      <Thumb src={f.url ?? f.panel_url} alt={`Shot ${n}`} status={f.status} dim={!f.url && !!f.panel_url} empty="Waiting for sketch"
        badge={!f.url && f.panel_url ? <Pill cls="p-mute">Sketch preview</Pill> : undefined} />
      <div className="place-body">
        <div className="row" style={{ gap: 6 }}><b>Shot {n}</b>{finalPill(f)}</div>
        <p className="faint small clamp-2">{f.shot.action}</p>
        {f.status === "stale" && <span className="warn-text small">{f.stale_reason ? `${f.stale_reason}, so this is out of date.` : "This image is out of date."}</span>}
        {f.status === "failed" && f.error && <span className="err-text small clamp-2" title={f.error}>{f.error}</span>}
        <div className="hstack" style={{ gap: 6, marginTop: "auto" }}>
          {f.status === "needs_approval" && (
            <>
              <ActionButton kind="success" size="sm" icon="check" onClick={async () => { await api.approveFinal(pid, f.shot.id); toast.ok(`Shot ${n} approved`); onChange(); }}>Approve</ActionButton>
              <CommentAction icon="refresh" label="Change" submitLabel="Redo" placeholder="What should change?"
                onSubmit={async (c) => { await api.rejectFinal(pid, f.shot.id, c); onChange(); }} />
            </>
          )}
          {(f.status === "stale" || f.status === "pending") && f.panel_url && (
            <ActionButton kind="soft" size="sm" icon={f.status === "stale" ? "refresh" : "sparkles"}
              onClick={async () => { await api.renderFinals(pid, sc.id, qual, [f.shot.id]); onChange(); }}>{f.status === "stale" ? "Update" : "Make this one"}</ActionButton>
          )}
          {f.status === "failed" && (
            <ActionButton kind="soft" size="sm" icon="refresh" onClick={async () => { await api.retryFinal(pid, f.shot.id); onChange(); }}>Try again</ActionButton>
          )}
          {f.status === "approved" && (
            <>
              {f.url && <a className="btn btn-sm" href={f.url} download={`${f.shot.id}.png`}><Icon name="download" size={14} />Download</a>}
              <CommentAction kind="text" label="Change" submitLabel="Redo" placeholder="What should change?"
                onSubmit={async (c) => { await api.rejectFinal(pid, f.shot.id, c); onChange(); }} />
            </>
          )}
        </div>
      </div>
    </article>
  );
}
