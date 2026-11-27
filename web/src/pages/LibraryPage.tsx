import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, isBusy, LibraryItem, LibraryView } from "../api";
import { Icon } from "../components/icons";
import { IssueList, ScoreRing } from "../components/check";
import { ActionButton, CommentAction, Loading, NextBar, OkDot, StatusPill, Tabs, Thumb, UploadButton, useLightbox, useToast } from "../components/ui";

type Filter = "all" | "characters" | "places";
type Group = LibraryView["characters"][number];

const allItems = (d: LibraryView) =>
  [...d.characters.flatMap((c) => [c.base, ...c.reactions]), ...d.locations.map((l) => l.item)].filter(Boolean) as LibraryItem[];

export default function LibraryPage({ pid }: { pid: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [filter, setFilter] = useState<Filter>("all");
  const q = useQuery({
    queryKey: ["library", pid], queryFn: () => api.library(pid),
    refetchInterval: (s) => (s.state.data && allItems(s.state.data).some((i) => isBusy(i.status)) ? 2000 : false),
  });
  const refresh = () => ["library", "overview"].forEach((k) => qc.invalidateQueries({ queryKey: [k, pid] }));

  if (q.isLoading || !q.data) return <Loading />;
  const d = q.data;
  if (!d.plan_approved) {
    return (
      <NextBar step={2} tone="warn" icon="list" title="Approve the plan first" text="Characters and places come from the approved shot plan.">
        <Link className="btn btn-primary" to="../plan"><Icon name="arrowLeft" size={15} />Go to the plan</Link>
      </NextBar>
    );
  }

  const items = allItems(d);
  const approved = items.filter((i) => i.status === "approved").length;
  const waiting = items.filter((i) => i.status === "needs_approval");
  const busy = items.filter((i) => isBusy(i.status));
  const missing = [...d.characters.map((c) => c.base), ...d.locations.map((l) => l.item)]
    .filter((i): i is LibraryItem => !!i && i.versions.length === 0 && !isBusy(i.status));
  const exprTodo = d.characters.filter((g) => !g.reactions_locked && g.reactions.some((r) => r.status !== "approved" && !isBusy(r.status) && r.versions.length === 0));
  const progress = { done: approved, total: items.length, label: "approved" };

  let bar;
  if (missing.length) {
    bar = (
      <NextBar step={2} icon="sparkles" progress={progress} title={`Create pictures for ${missing.length} character${missing.length > 1 ? "s" : ""} and places`}
        text="We draw each one from its description in the plan. Prefer your own art? Use Upload on any card instead.">
        <ActionButton kind="primary" icon="sparkles" onClick={async () => {
          for (const i of missing) await api.generateItem(pid, i.id);
          toast.info(`Making ${missing.length} pictures`); refresh();
        }}>Create {missing.length} picture{missing.length > 1 ? "s" : ""}</ActionButton>
      </NextBar>
    );
  } else if (waiting.length) {
    bar = (
      <NextBar step={2} tone="warn" icon="eye" progress={progress} title={`Check ${waiting.length} new picture${waiting.length > 1 ? "s" : ""} and approve them`}
        text="Each picture marked “Check & approve” is waiting for you. Not right? Click Change and say what to fix in one line.">
        {waiting.length > 1 && <ActionButton kind="primary" icon="check" onClick={async () => {
          for (const i of waiting) await api.approveItem(pid, i.id);
          toast.ok(`Approved ${waiting.length} pictures`); refresh();
        }}>Approve all {waiting.length}</ActionButton>}
      </NextBar>
    );
  } else if (exprTodo.length) {
    bar = (
      <NextBar step={2} icon="user" progress={progress} title="Create the expressions"
        text={`Each character gets a face for every feeling in the story. All of a character’s faces are drawn on one sheet: ${exprTodo.length} image${exprTodo.length > 1 ? "s" : ""} in total.`}>
        <ActionButton kind="primary" icon="sparkles" onClick={async () => {
          for (const g of exprTodo) await api.generateReactionSheet(pid, g.character.id);
          toast.info("Drawing expressions"); refresh();
        }}>Create expressions</ActionButton>
      </NextBar>
    );
  } else if (busy.length) {
    bar = <NextBar step={2} tone="wait" icon="clock" progress={progress} title={`Creating ${busy.length} picture${busy.length > 1 ? "s" : ""}…`}
      text="They appear below as soon as they’re ready. You can leave this page and come back." />;
  } else if (approved === items.length) {
    bar = (
      <NextBar step={2} tone="ok" icon="check" progress={progress} title="Every character and place is approved" text="Next: sketch the storyboard, one scene at a time.">
        <Link className="btn btn-primary" to="../storyboard">Next: storyboard<Icon name="arrowRight" size={15} /></Link>
      </NextBar>
    );
  } else {
    bar = <NextBar step={2} tone="warn" icon="alert" progress={progress} title="Some pictures need attention" text="Look for cards marked Failed or Out of date and use Try again." />;
  }

  return (
    <>
      {bar}
      <Tabs label="Show" value={filter} onChange={setFilter} items={[
        { value: "all", label: "Everything" },
        { value: "characters", label: "Characters", n: d.characters.length },
        { value: "places", label: "Places", n: d.locations.length },
      ]} />

      {filter !== "places" && d.characters.length > 0 && (
        <section className="card">
          <div className="card-head">
            <h3 className="section-title">Characters</h3>
            <span className="faint small">Main picture first, then one expression per feeling</span>
          </div>
          {d.characters.map((g) => <CharacterRow key={g.character.id} pid={pid} g={g} onChange={refresh} />)}
        </section>
      )}

      {filter !== "characters" && d.locations.length > 0 && (
        <section className="stack">
          <div className="section-head"><h3 className="section-title">Places</h3><span className="faint small">One wide picture per place</span></div>
          <div className="grid g-places">
            {d.locations.map((g) => g.item && (
              <article key={g.location.id} className="card place-card">
                <Thumb src={g.item.current_url} alt={g.location.name} status={g.item.status} empty="No picture yet" />
                <div className="place-body">
                  <div className="row" style={{ gap: 6 }}><b>{g.location.name}</b><StatusPill status={g.item.status} /></div>
                  <p className="faint small clamp-2">{g.location.description}</p>
                  <ItemNotes item={g.item} />
                  <ItemCheck pid={pid} item={g.item} onChange={refresh} />
                  <ItemActions pid={pid} item={g.item} onChange={refresh} />
                </div>
              </article>
            ))}
          </div>
        </section>
      )}
    </>
  );
}

function CharacterRow({ pid, g, onChange }: { pid: string; g: Group; onChange: () => void }) {
  const toast = useToast();
  const lightbox = useLightbox();
  const [sel, setSel] = useState<string | null>(null);
  const base = g.base;
  const name = g.character.name;
  const todo = g.reactions.filter((r) => r.status !== "approved" && !isBusy(r.status)).length;
  const bv = base?.versions.find((v) => v.n === base.approved_version);
  const baseWrong = !!(base?.check && bv && bv.source !== "uploaded" && base.check.version === bv.n && !base.check.error && base.check.score < 50);
  const waiting = g.reactions.filter((r) => r.status === "needs_approval").length;
  const done = g.reactions.filter((r) => r.status === "approved").length;
  const selected = g.reactions.find((r) => r.id === sel);

  return (
    <div className="char-row">
      <Thumb src={base?.current_url} alt={`${name}, main picture`} status={base?.status} aspect="1 / 1" className="r-10" position="top"
        empty="No picture yet" badge={base?.status === "approved" ? <OkDot /> : undefined} />
      <div className="stack" style={{ gap: 10, minWidth: 0 }}>
        <div className="vstack" style={{ gap: 2 }}>
          <div className="hstack"><b style={{ fontSize: 15 }}>{name}</b>{base && <StatusPill status={base.status} />}</div>
          <span className="faint small clamp-2">{g.character.description}</span>
        </div>
        {base && <ItemNotes item={base} />}
        {base && <ItemCheck pid={pid} item={base} onChange={onChange} />}
        {base && <ItemActions pid={pid} item={base} onChange={onChange} />}

        {g.reactions.length > 0 && (
          <div className="stack" style={{ gap: 8, paddingTop: 10, borderTop: "1px dashed var(--line)" }}>
            <div className="row" style={{ gap: 8 }}>
              <span className="small"><b>Expressions</b> <span className="faint">· {done} of {g.reactions.length} approved</span></span>
              {!g.reactions_locked && !baseWrong && (
                <div className="hstack" style={{ gap: 6 }}>
                  {waiting > 1 && <ActionButton size="sm" icon="check" onClick={async () => {
                    const r = await api.approveAllReactions(pid, g.character.id); toast.ok(`Approved ${r.approved.length} expressions`); onChange();
                  }}>Approve all {waiting}</ActionButton>}
                  {todo > 0 && <CommentAction kind="soft" icon="sparkles" optional label={`Create ${todo} expression${todo > 1 ? "s" : ""}`}
                    submitLabel="Create" placeholder="Optional note, e.g. keep the same scarf"
                    onSubmit={async (c) => { await api.generateReactionSheet(pid, g.character.id, c); toast.info(`Drawing ${name}’s expressions`); onChange(); }} />}
                </div>
              )}
            </div>
            {baseWrong && (
              <span className="err-text small">
                <Icon name="alert" size={13} style={{ verticalAlign: -2, marginRight: 4 }} />
                {name}’s main picture shows the wrong subject, and these expressions copy it. Use <b>Start over</b> on the main picture first; the expressions can be redrawn afterwards.
              </span>
            )}
            {g.reactions_locked ? (
              <span className="faint small"><Icon name="clock" size={13} style={{ verticalAlign: -2, marginRight: 4 }} />Approve {name}’s main picture first. Expressions are drawn from it so the face stays the same.</span>
            ) : (
              <>
                <div className="expr-grid">
                  {g.reactions.map((r) => (
                    <button key={r.id} className={`pick${sel === r.id ? " sel" : ""}`} aria-pressed={sel === r.id}
                      onClick={() => setSel(sel === r.id ? null : r.id)} title={`${r.emotion}: click for options`}>
                      <div className="frame">
                        {r.current_url ? <img src={r.current_url} alt={`${name} looking ${r.emotion}`} data-lightbox="" onDoubleClick={() => lightbox(r.current_url!, `${name} looking ${r.emotion}`)} />
                          : <div className="thumb-empty">{isBusy(r.status) ? <span className="spinner" /> : <Icon name="user" size={16} />}</div>}
                        {r.current_url && isBusy(r.status) && <div className="thumb-overlay"><span className="spinner" /></div>}
                        <span className="thumb-badge">
                          {r.check && r.check.version === r.versions[r.versions.length - 1]?.n && r.versions[r.versions.length - 1]?.source !== "uploaded" && r.check.score < 80 && !r.check.error
                            ? <span className="dot dot-20 dot-err-soft" title={`AI check ${r.check.score}/100`}><Icon name="alert" size={11} /></span>
                            : r.status === "approved" && <OkDot size={18} />}
                          {r.status === "needs_approval" && <span className="dot dot-20 dot-warn-soft" title="Check & approve"><Icon name="eye" size={11} /></span>}
                          {(r.status === "failed" || r.status === "stale") && <span className="dot dot-20 dot-warn-soft"><Icon name="alert" size={11} /></span>}
                        </span>
                      </div>
                      <span className="pick-label cap">{r.emotion}</span>
                    </button>
                  ))}
                </div>
                {selected ? (
                  <div className="inline-panel" style={{ flexDirection: "column", alignItems: "stretch" }}>
                    <div className="hstack"><b className="cap small">{selected.emotion}</b><StatusPill status={selected.status} /></div>
                    <ItemCheck pid={pid} item={selected} onChange={onChange} />
                    <ItemActions pid={pid} item={selected} onChange={onChange} />
                  </div>
                ) : waiting > 0 && <span className="faint small">Click a face to approve it or ask for a change.</span>}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function ItemNotes({ item }: { item: LibraryItem }) {
  const cur = item.versions[item.versions.length - 1];
  if (item.status === "stale") return <span className="warn-text small">{item.error || "Its main picture changed, so this needs a new version."}</span>;
  if (item.status === "failed" && item.error) return <span className="err-text small clamp-2" title={item.error}>{item.error}</span>;
  if (cur?.comment && item.status === "needs_approval") return <span className="faint small">Changed: “{cur.comment}”</span>;
  return null;
}

function ItemActions({ pid, item, onChange }: { pid: string; item: LibraryItem; onChange: () => void }) {
  const toast = useToast();
  if (isBusy(item.status)) return <span className="faint small hstack" style={{ gap: 6 }}><span className="spinner" style={{ width: 12, height: 12 }} />Creating a new version…</span>;
  const has = item.versions.length > 0;
  const upload = <UploadButton label={has ? "Replace with my own image" : "Upload my own"} iconOnly={has} kind={has ? "text" : "secondary"}
    accept="image/png,image/jpeg,image/webp"
    onFile={async (f) => { await api.uploadItem(pid, item.id, f); toast.ok("Uploaded. Check it and approve."); onChange(); }} />;

  if (!has) {
    return (
      <div className="hstack" style={{ gap: 6 }}>
        <ActionButton kind="primary" size="sm" icon="sparkles" onClick={async () => { await api.generateItem(pid, item.id); onChange(); }}>Create</ActionButton>
        {upload}
      </div>
    );
  }
  const startOver = (
    <CommentAction kind="text" icon="sparkles" optional label="Start over" submitLabel="Create new"
      placeholder="Optional: what it should look like" hint="Draws a brand-new picture from the description instead of editing this one."
      onSubmit={async (c) => { await api.generateItem(pid, item.id, c, undefined, true); toast.info("Creating a new picture"); onChange(); }} />
  );
  const cur = item.versions[item.versions.length - 1];
  const wrong = !!(item.check && cur && cur.source !== "uploaded" && item.check.version === cur.n && !item.check.error && item.check.score < 50);
  if (item.status === "needs_approval") {
    return (
      <div className="hstack" style={{ gap: 6 }}>
        {wrong
          ? <ActionButton kind="secondary" size="sm" icon="check" title="The AI check says this shows the wrong subject"
              onClick={async () => { await api.approveItem(pid, item.id); toast.ok("Approved"); onChange(); }}>Approve anyway</ActionButton>
          : <ActionButton kind="success" size="sm" icon="check" onClick={async () => { await api.approveItem(pid, item.id); toast.ok("Approved"); onChange(); }}>Approve</ActionButton>}
        <CommentAction icon="refresh" label="Change" submitLabel="Redo" placeholder="What should change? e.g. shorter hair"
          onSubmit={async (c) => { await api.rejectItem(pid, item.id, c); onChange(); }} />
        {startOver}
        {upload}
      </div>
    );
  }
  if (item.status === "approved") {
    return (
      <div className="hstack" style={{ gap: 6 }}>
        <CommentAction kind="text" icon="refresh" label="Change" submitLabel="Redo" placeholder="What should change?"
          hint="Shots already made with this picture will be marked out of date."
          onSubmit={async (c) => { await api.rejectItem(pid, item.id, c); onChange(); }} />
        {startOver}
        {upload}
      </div>
    );
  }
  return (
    <div className="hstack" style={{ gap: 6 }}>
      <ActionButton kind="primary" size="sm" icon="refresh" onClick={async () => { await api.generateItem(pid, item.id); onChange(); }}>Try again</ActionButton>
      {upload}
    </div>
  );
}

function ItemCheck({ pid, item, onChange }: { pid: string; item: LibraryItem; onChange: () => void }) {
  const toast = useToast();
  const cur = item.versions[item.versions.length - 1];
  if (!cur || isBusy(item.status)) return null;
  if (cur.source === "uploaded") {
    return <span className="faint small hstack" style={{ gap: 6 }}><Icon name="upload" size={13} />Your own picture · used as is, not scored</span>;
  }
  if (item.checking) return <span className="faint small hstack" style={{ gap: 6 }}><span className="spinner" style={{ width: 12, height: 12 }} />AI is checking this picture…</span>;
  const c = item.check && item.check.version === cur.n ? item.check : null;
  if (!c) {
    return <ActionButton kind="text" size="sm" icon="eye" onClick={async () => { await api.checkItem(pid, item.id); onChange(); }}>Run AI check</ActionButton>;
  }
  if (c.error) {
    return (
      <span className="hstack small" style={{ gap: 6 }}>
        <span className="err-text">AI check failed</span>
        <ActionButton kind="text" size="sm" onClick={async () => { await api.checkItem(pid, item.id); onChange(); }}>Try again</ActionButton>
      </span>
    );
  }
  const canFix = c.issues.length > 0 && c.suggested_fix && ["needs_approval", "approved", "stale"].includes(item.status);
  return (
    <div className="check-box">
      <div className="check-head">
        <ScoreRing score={c.score} size={28} />
        <span className="grow1">
          <b>AI check {c.score}/100</b>
          <span className="faint"> · {c.issues.length ? `${c.issues.length} problem${c.issues.length > 1 ? "s" : ""}` : c.score >= 100 ? "flawless" : "no problems found"}</span>
        </span>
      </div>
      <IssueList issues={c.issues} compact />
      {canFix && c.score < 50 && (
        <>
          <span className="small err-text">This picture doesn’t show the right subject, so editing it won’t help.</span>
          <ActionButton kind="soft" size="sm" icon="sparkles" title={c.suggested_fix}
            onClick={async () => { await api.generateItem(pid, item.id, c.suggested_fix.slice(0, 600), undefined, true); toast.info("Creating a new picture"); onChange(); }}>
            Start over with AI
          </ActionButton>
        </>
      )}
      {canFix && c.score >= 50 && (
        <ActionButton kind="soft" size="sm" icon="sparkles" title={c.suggested_fix}
          onClick={async () => { await api.rejectItem(pid, item.id, c.suggested_fix.slice(0, 600)); toast.info("Fixing the picture"); onChange(); }}>
          Fix {c.issues.length > 1 ? `all ${c.issues.length}` : "it"} with AI
        </ActionButton>
      )}
    </div>
  );
}
