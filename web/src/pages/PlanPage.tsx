import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, CharacterKind, LibraryView, Plan, Shot } from "../api";
import { Icon } from "../components/icons";
import { ActionButton, AutoTextarea, Avatar, Loading, NextBar, Portal, Tabs, useToast } from "../components/ui";

export default function PlanPage({ pid }: { pid: string }) {
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({
    queryKey: ["plan", pid], queryFn: () => api.plan(pid),
    refetchInterval: (s) => (s.state.data?.parse_status === "parsing" ? 1500 : false),
  });
  const lib = useQuery({ queryKey: ["library", pid], queryFn: () => api.library(pid), enabled: q.data?.status === "approved" });
  const [draft, setDraft] = useState<Plan | null>(null);
  const [openEntity, setOpenEntity] = useState<EntityTarget | null>(null);
  const [editShot, setEditShot] = useState<string | null>(null);
  const [raw, setRaw] = useState<string | null>(null);
  const [sceneIdx, setSceneIdx] = useState(0);
  const dirty = useMemo(() => !!draft && !!q.data && JSON.stringify(draft) !== JSON.stringify(q.data.plan), [draft, q.data]);

  const refresh = () => {
    ["plan", "overview", "library"].forEach((k) => qc.invalidateQueries({ queryKey: [k, pid] }));
    qc.invalidateQueries({ queryKey: ["projects"] });
  };
  if (q.isLoading || !q.data) return <Loading />;
  const v = q.data;

  if (v.parse_status === "parsing") {
    return <ParsingScreen pid={pid} />;
  }
  if (v.parse_status === "failed") {
    return (
      <NextBar step={1} tone="warn" icon="alert" title="We couldn’t read this PDF" text={v.parse_error}>
        <ActionButton kind="primary" icon="refresh" onClick={async () => { await api.reparse(pid); refresh(); }}>Try again</ActionButton>
      </NextBar>
    );
  }

  const plan = draft ?? v.plan;
  const edit = (fn: (p: Plan) => void) => { const next = structuredClone(plan); fn(next); setDraft(next); };
  const si = Math.min(sceneIdx, Math.max(0, plan.scenes.length - 1));
  const sc = plan.scenes[si];
  const nShots = plan.scenes.reduce((a, s) => a + s.shots.length, 0);
  const charName = (id: string) => plan.characters.find((c) => c.id === id)?.name ?? id;
  const locName = (id: string) => plan.locations.find((l) => l.id === id)?.name;
  const avatar = (cid: string) => lib.data?.characters.find((g) => g.character.id === cid)?.base?.current_url;
  const locImg = (lid: string) => lib.data?.locations.find((g) => g.location.id === lid)?.item?.current_url;
  const save = async (p: Plan = plan) => {
    await api.savePlan(pid, p);
    toast.ok("Changes saved");
    setDraft(null); setEditShot(null); setRaw(null); refresh();
  };
  const cancel = () => { setDraft(null); setEditShot(null); setRaw(null); };

  return (
    <>
      {dirty ? (
        <NextBar step={1} tone="warn" icon="edit" title="Save your changes"
          text={v.status === "approved" ? "Saving sends the plan back for one more check and approval." : "Save before you approve the plan."}>
          <button className="btn" onClick={cancel}>Discard</button>
          <ActionButton kind="primary" icon="check" onClick={() => save()}>Save changes</ActionButton>
        </NextBar>
      ) : v.status === "approved" ? (
        <NextBar step={1} tone="ok" icon="check" title="The plan is approved"
          text="Next: create a picture of each character and place, so they look the same in every shot.">
          <Link className="btn btn-primary" to="../library">Next: characters &amp; places<Icon name="arrowRight" size={15} /></Link>
        </NextBar>
      ) : (
        <NextBar step={1} icon="list" title="Check what we read from your PDF, then approve it"
          text={<>We found <b>{plan.characters.length} characters</b>, <b>{plan.locations.length} places</b> and <b>{nShots} shots</b>. Fix anything wrong with the pencil icons, then approve.</>}>
          {v.source_url && <a className="btn" href={v.source_url} target="_blank" rel="noreferrer"><Icon name="file" size={15} />Open PDF</a>}
          <ActionButton kind="text" icon="refresh" title="Read the PDF again from scratch (about 10¢)"
            onClick={async () => { await api.reparse(pid); toast.info("Reading the PDF again"); refresh(); }}>Read again</ActionButton>
          <ActionButton kind="primary" icon="check" onClick={async () => { await api.approvePlan(pid); toast.ok("Plan approved"); refresh(); }}>Approve the plan</ActionButton>
        </NextBar>
      )}

      {raw !== null ? (
        <section className="card card-pad stack">
          <div className="row"><h3 className="section-title">Edit as JSON</h3><span className="faint small">Advanced</span></div>
          <textarea className="code" rows={24} value={raw} onChange={(e) => setRaw(e.target.value)} spellCheck={false} aria-label="Plan JSON" />
          <div className="hstack" style={{ justifyContent: "flex-end" }}>
            <button className="btn" onClick={cancel}>Cancel</button>
            <ActionButton kind="primary" onClick={() => save(JSON.parse(raw) as Plan)}>Save JSON</ActionButton>
          </div>
        </section>
      ) : (
        <div className="split">
          <section className="card pane">
            <div className="card-head">
              <h3 className="section-title">Shots <span className="faint" style={{ fontWeight: 500 }}>· {plan.scenes.length} scenes · {nShots} shots</span></h3>
              {v.source_url && v.status === "approved" && <a className="btn btn-text btn-sm" href={v.source_url} target="_blank" rel="noreferrer"><Icon name="file" size={14} />Original PDF</a>}
            </div>
            <div style={{ padding: "12px 16px 4px" }}>
              <Tabs label="Scenes" value={String(si)} onChange={(v) => { setSceneIdx(Number(v)); setEditShot(null); }}
                items={plan.scenes.map((s, i) => ({ value: String(i), label: `Scene ${i + 1}`, n: s.shots.length }))} />
            </div>
            {sc && (
              <>
                <div className="row" style={{ padding: "8px 16px 10px", borderBottom: "1px solid var(--line)" }}>
                  <b>{sc.title || `Scene ${si + 1}`}</b>
                  <span className="faint small">{sc.shots.length} shot{sc.shots.length === 1 ? "" : "s"}</span>
                </div>
                <ol className="list pane-body">
                  {sc.shots.map((sh, hi) => (
                    <li key={sh.id} className="shot-row">
                      <span className="shot-num">{hi + 1}</span>
                      {editShot === sh.id ? (
                        <ShotEditor shot={sh} onChange={(fn) => edit((p) => fn(p.scenes[si].shots[hi]))} onDone={() => setEditShot(null)} />
                      ) : (
                        <div className="grow1 vstack" style={{ gap: 3 }}>
                          <span className="faint small" style={{ fontWeight: 600 }}>{[sh.framing, locName(sh.location_id), sh.time_of_day].filter(Boolean).join(" · ") || sh.id}</span>
                          <span>{sh.action}</span>
                          {sh.dialogue && <span className="dialogue small">“{cleanDialogue(sh.dialogue)}”</span>}
                          {sh.character_ids.length > 0 && (
                            <div className="hstack" style={{ gap: 4, marginTop: 2 }}>
                              {sh.character_ids.map((cid) => {
                                const emo = sh.reactions.find((r) => r.character_id === cid)?.emotion;
                                return <span key={cid} className="chip">{charName(cid)}{emo ? ` · ${emo}` : ""}</span>;
                              })}
                            </div>
                          )}
                        </div>
                      )}
                      {editShot !== sh.id && (
                        <button className="btn btn-text btn-sm btn-icon" aria-label={`Edit shot ${hi + 1}`} title="Edit this shot" onClick={() => setEditShot(sh.id)}><Icon name="edit" size={15} /></button>
                      )}
                    </li>
                  ))}
                </ol>
              </>
            )}
          </section>

          <div className="stack pane-scroll">
            <section className="card card-pad stack" style={{ gap: 8 }}>
              <StylePicker value={plan.style} hasPictures={!!lib.data?.characters.some((g) => g.base?.versions.length)}
                onChange={(style) => edit((p) => { p.style = style; })} />
              <button className="btn btn-text btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => setRaw(JSON.stringify(plan, null, 2))}>Edit as JSON (advanced)</button>
            </section>
            <section className="card">
              <div className="card-head">
                <h3 className="section-title">Characters <span className="faint" style={{ fontWeight: 500 }}>· {plan.characters.length}</span></h3>
                <span className="faint small">Click to view &amp; edit</span>
              </div>
              <div style={{ padding: "4px 8px 8px" }}>
                {plan.characters.map((c, i) => (
                  <button key={c.id} className="person person-btn" onClick={() => setOpenEntity({ kind: "char", index: i })}>
                    <Avatar name={c.name} src={avatar(c.id)} />
                    <span className="grow1 vstack" style={{ gap: 0 }}>
                      <span style={{ fontWeight: 600 }}>{c.name} {c.kind && c.kind !== "person" && <span className="chip" style={{ height: 18, fontSize: 11, marginLeft: 4 }}>{c.kind}</span>}</span>
                      <span className="faint small clamp-2">{c.description}</span>
                    </span>
                    <Icon name="arrowRight" size={14} className="person-go" />
                  </button>
                ))}
              </div>
            </section>
            <section className="card">
              <div className="card-head">
                <h3 className="section-title">Places <span className="faint" style={{ fontWeight: 500 }}>· {plan.locations.length}</span></h3>
                <span className="faint small">Click to view &amp; edit</span>
              </div>
              <div style={{ padding: "4px 8px 8px" }}>
                {plan.locations.map((l, i) => (
                  <button key={l.id} className="person person-btn" onClick={() => setOpenEntity({ kind: "loc", index: i })}>
                    {locImg(l.id)
                      ? <img src={locImg(l.id)!} alt="" style={{ width: 44, height: 30, borderRadius: 6, objectFit: "cover", flex: "none" }} />
                      : <span className="icon-tile" style={{ width: 44, height: 30, borderRadius: 6 }}><Icon name="map" size={15} /></span>}
                    <span className="grow1 vstack" style={{ gap: 0 }}>
                      <span style={{ fontWeight: 600 }}>{l.name}</span>
                      <span className="faint small clamp-2">{l.description}</span>
                    </span>
                    <Icon name="arrowRight" size={14} className="person-go" />
                  </button>
                ))}
              </div>
            </section>
          </div>
        </div>
      )}
      {openEntity && (
        <EntityModal plan={plan} lib={lib.data} target={openEntity} approved={v.status === "approved"}
          onClose={() => setOpenEntity(null)}
          onSave={async (name, description, kind) => {
            const next = structuredClone(plan);
            const list = openEntity.kind === "char" ? next.characters : next.locations;
            list[openEntity.index].name = name;
            list[openEntity.index].description = description;
            if (openEntity.kind === "char") next.characters[openEntity.index].kind = kind as CharacterKind;
            await save(next);
            setOpenEntity(null);
          }}
          onRemove={async () => {
            // Remove a character or place from the plan and from every shot that used it.
            const next = structuredClone(plan);
            if (openEntity.kind === "char") {
              const id = next.characters[openEntity.index].id;
              next.characters.splice(openEntity.index, 1);
              for (const sc of next.scenes) for (const sh of sc.shots) {
                sh.character_ids = sh.character_ids.filter((c) => c !== id);
                sh.reactions = sh.reactions.filter((r) => r.character_id !== id);
              }
            } else {
              const id = next.locations[openEntity.index].id;
              next.locations.splice(openEntity.index, 1);
              for (const sc of next.scenes) for (const sh of sc.shots) if (sh.location_id === id) sh.location_id = "";
            }
            await save(next);
            setOpenEntity(null);
          }} />
      )}
    </>
  );
}

type EntityTarget = { kind: "char" | "loc"; index: number };

function EntityModal({ plan, lib, target, approved, onClose, onSave, onRemove }: {
  plan: Plan; lib?: LibraryView; target: EntityTarget; approved: boolean;
  onClose: () => void; onSave: (name: string, description: string, kind: string) => Promise<void>;
  onRemove: () => Promise<void>;
}) {
  const isChar = target.kind === "char";
  const entity = isChar ? plan.characters[target.index] : plan.locations[target.index];
  const [name, setName] = useState(entity.name);
  const [desc, setDesc] = useState(entity.description);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [kind, setKind] = useState<string>(isChar ? ((entity as Plan["characters"][number]).kind || "creature") : "");
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const group = isChar ? lib?.characters.find((g) => g.character.id === entity.id) : undefined;
  const item = isChar ? group?.base : lib?.locations.find((g) => g.location.id === entity.id)?.item;
  const img = item?.current_url;
  const shots = plan.scenes.flatMap((sc, si) => sc.shots.map((sh, hi) => ({ sh, label: `S${si + 1}·${hi + 1}` })))
    .filter(({ sh }) => (isChar ? sh.character_ids.includes(entity.id) : sh.location_id === entity.id));
  const emotions = isChar ? (entity as Plan["characters"][number]).reactions : [];
  const dirty = name.trim() !== entity.name || desc !== entity.description
    || (isChar && kind !== ((entity as Plan["characters"][number]).kind || "creature"));

  return (
    <Portal>
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal aria-label={entity.name}>
        <div className="card-head">
          <b style={{ fontSize: 15 }}>{isChar ? "Character" : "Place"} <span className="faint" style={{ fontWeight: 500 }}>· {entity.name}</span></b>
          <button className="btn btn-text btn-sm btn-icon" aria-label="Close" onClick={onClose}><Icon name="x" size={16} /></button>
        </div>
        <div className="modal-body">
          <div className="stack" style={{ gap: 10 }}>
            <div className="thumb r-10" style={{ aspectRatio: isChar ? "1 / 1" : "16 / 9" }}>
              {img ? <img src={img} alt={entity.name} style={{ objectPosition: "top", cursor: "default" }} />
                : <div className="thumb-empty"><Icon name={isChar ? "user" : "map"} size={22} />No picture yet<span className="faint" style={{ fontWeight: 400 }}>It’s created in step 2</span></div>}
            </div>
            {item && <span className="faint small">Picture status: <b>{item.status.replace("_", " ")}</b></span>}
            {group && group.reactions.some((r) => r.current_url) && (
              <div className="stack" style={{ gap: 6 }}>
                <span className="small" style={{ fontWeight: 600 }}>Expressions</span>
                <div className="hstack" style={{ gap: 6 }}>
                  {group.reactions.filter((r) => r.current_url).map((r) => (
                    <img key={r.id} src={r.current_url!} alt={r.emotion} title={r.emotion}
                      style={{ width: 48, height: 48, borderRadius: 8, objectFit: "cover", objectPosition: "top" }} />
                  ))}
                </div>
              </div>
            )}
          </div>
          <div className="stack" style={{ gap: 12 }}>
            <label className="field">Name<input value={name} onChange={(e) => setName(e.target.value)} /></label>
            {isChar && (
              <label className="field">What is it?
                <select value={kind} onChange={(e) => setKind(e.target.value)} style={{ width: "100%" }}>
                  <option value="person">A person</option>
                  <option value="animal">An animal</option>
                  <option value="creature">A creature (talking animal, insect, fantasy being)</option>
                  <option value="object">An object (paper boat, letter, toy)</option>
                  <option value="group">A group (crowd, flock, swarm)</option>
                  <option value="effect">An effect (lights, glow, magic, weather)</option>
                </select>
                <span className="faint" style={{ fontWeight: 400 }}>Decides how its picture is drawn: e.g. a swarm of lights is never drawn as a person.</span>
              </label>
            )}
            <label className="field">Description (how they look)
              <AutoTextarea minRows={4} maxRows={16} value={desc} onChange={(e) => setDesc(e.target.value)} />
            </label>
            {emotions.length > 0 && (
              <div className="vstack" style={{ gap: 6 }}>
                <span className="small" style={{ fontWeight: 600, color: "var(--muted)" }}>Feelings in the story</span>
                <div className="hstack" style={{ gap: 4 }}>{emotions.map((e) => <span key={e} className="chip cap">{e}</span>)}</div>
              </div>
            )}
            <div className="vstack" style={{ gap: 6 }}>
              <span className="small" style={{ fontWeight: 600, color: "var(--muted)" }}>Appears in {shots.length} shot{shots.length === 1 ? "" : "s"}</span>
              <div className="hstack" style={{ gap: 4 }}>{shots.map(({ sh, label }) => <span key={sh.id} className="chip" title={sh.action}>{label}</span>)}</div>
            </div>
            {dirty && (
              <span className="warn-text small">
                {approved ? "Saving sends the plan back for approval. " : ""}{img ? "The existing picture isn’t redrawn. Use Change in step 2 if it should look different." : ""}
              </span>
            )}
          </div>
        </div>
        <div className="modal-foot">
          <span style={{ marginRight: "auto" }}>
            {confirmRemove ? (
              <span className="hstack" style={{ gap: 6 }}>
                <span className="small err-text">Remove {entity.name} from every shot?</span>
                <ActionButton kind="danger" size="sm" icon="trash" onClick={onRemove}>Remove</ActionButton>
                <button className="btn btn-text btn-sm" onClick={() => setConfirmRemove(false)}>Keep</button>
              </span>
            ) : (
              <button className="btn btn-text btn-sm" style={{ color: "var(--err)" }} onClick={() => setConfirmRemove(true)}>
                <Icon name="trash" size={14} />Remove from plan
              </button>
            )}
          </span>
          <button className="btn" onClick={onClose}>Cancel</button>
          <ActionButton kind="primary" icon="check" disabled={!dirty || !name.trim()} onClick={() => onSave(name.trim(), desc, kind)}>Save</ActionButton>
        </div>
      </div>
    </div>
    </Portal>
  );
}

function ShotEditor({ shot, onChange, onDone }: { shot: Shot; onChange: (fn: (s: Shot) => void) => void; onDone: () => void }) {
  return (
    <div className="stack grow1" style={{ gap: 8 }}>
      <label className="field">Framing<input value={shot.framing} onChange={(e) => onChange((s) => { s.framing = e.target.value; })} /></label>
      <label className="field">What happens<textarea rows={2} value={shot.action} onChange={(e) => onChange((s) => { s.action = e.target.value; })} /></label>
      <label className="field">Dialogue<input value={shot.dialogue} onChange={(e) => onChange((s) => { s.dialogue = e.target.value; })} /></label>
      <div className="hstack" style={{ justifyContent: "flex-end" }}><button className="btn btn-sm" onClick={onDone}>Done</button></div>
    </div>
  );
}

function ParsingScreen({ pid }: { pid: string }) {
  const ov = useQuery({ queryKey: ["overview", pid], queryFn: () => api.overview(pid), refetchInterval: 3000 });
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    const started = ov.data ? (ov.data.project.parse_started_at ?? ov.data.project.created_at) : Date.now() / 1000;
    const t0 = Date.now() - Math.max(0, Date.now() / 1000 - started) * 1000;
    const id = setInterval(() => setSecs(Math.floor((Date.now() - t0) / 1000)), 1000);
    return () => clearInterval(id);
  }, [ov.data]);
  const mmss = `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
  const file = ov.data?.project.source_filename;
  const slow = secs > 90;

  return (
    <>
      <section className="card parse-card">
        <div className="parse-doc" aria-hidden>
          <span className="parse-line" style={{ width: "70%" }} /><span className="parse-line" style={{ width: "90%" }} />
          <span className="parse-line" style={{ width: "55%" }} /><span className="parse-line" style={{ width: "80%" }} />
          <span className="parse-line" style={{ width: "65%" }} /><span className="parse-line" style={{ width: "85%" }} />
          <span className="parse-scan" />
        </div>
        <div className="stack" style={{ gap: 10, flex: 1, minWidth: 0 }}>
          <div className="vstack" style={{ gap: 2 }}>
            <span className="next-kicker">Step 1 of 5 · Please wait</span>
            <h2 style={{ fontSize: 18, fontWeight: 700 }}>Reading your Shot Planner</h2>
            <span className="muted small">
              {file ? <><Icon name="file" size={13} style={{ verticalAlign: -2 }} /> {file} · </> : null}
              <span className="tnum">{mmss}</span> elapsed
            </span>
          </div>
          <div className="parse-bar" role="progressbar" aria-label="Reading the PDF" aria-busy="true"><span /></div>
          <div className="vstack" style={{ gap: 6 }}>
            <span className="small" style={{ fontWeight: 600 }}>The AI is finding:</span>
            <div className="hstack" style={{ gap: 6 }}>
              <span className="chip parse-chip"><Icon name="user" size={13} />Characters &amp; how they look</span>
              <span className="chip parse-chip"><Icon name="map" size={13} />Places</span>
              <span className="chip parse-chip"><Icon name="film" size={13} />Scenes, shots &amp; dialogue</span>
              <span className="chip parse-chip"><Icon name="sparkles" size={13} />Each character’s feelings</span>
            </div>
          </div>
          <span className={`small ${slow ? "warn-text" : "faint"}`}>
            {slow ? "This is taking longer than usual. Large PDFs can need a couple of minutes." : "Usually under a minute. You can leave this page, and we’ll keep going."}
          </span>
        </div>
      </section>

      <div className="split" aria-hidden style={{ opacity: 0.7 }}>
        <section className="card">
          <div className="card-head"><span className="sk" style={{ width: 160, height: 14 }} /></div>
          <div style={{ padding: "12px 16px" }}><span className="sk" style={{ width: 240, height: 30, borderRadius: 10 }} /></div>
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="shot-row">
              <span className="sk" style={{ width: 26, height: 26, borderRadius: 7 }} />
              <div className="stack grow1" style={{ gap: 6 }}>
                <span className="sk" style={{ width: "35%", height: 10 }} />
                <span className="sk" style={{ width: `${70 + (i % 2) * 15}%`, height: 12 }} />
                <div className="hstack" style={{ gap: 4 }}><span className="sk" style={{ width: 80, height: 20, borderRadius: 99 }} /><span className="sk" style={{ width: 60, height: 20, borderRadius: 99 }} /></div>
              </div>
            </div>
          ))}
        </section>
        <section className="card">
          <div className="card-head"><span className="sk" style={{ width: 110, height: 14 }} /></div>
          <div style={{ padding: "8px 16px" }}>
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="person">
                <span className="sk" style={{ width: 32, height: 32, borderRadius: 99 }} />
                <div className="stack grow1" style={{ gap: 5 }}><span className="sk" style={{ width: "40%", height: 11 }} /><span className="sk" style={{ width: "85%", height: 9 }} /></div>
              </div>
            ))}
          </div>
        </section>
      </div>
    </>
  );
}

// Looks described in plain words (no studio names): image models follow these more reliably.
const STYLE_PRESETS: { id: string; label: string; style: string }[] = [
  { id: "3d", label: "3D animated film",
    style: "3D animated feature-film look: soft rounded shapes, expressive faces with big eyes, smooth stylised textures, warm cinematic lighting and rich colour." },
  { id: "classic", label: "Classic hand-drawn animation",
    style: "Classic hand-drawn 2D animation: clean expressive ink lines, painted watercolour-like backgrounds, gentle warm colours, storybook charm." },
  { id: "anime", label: "Anime",
    style: "Anime style: clean line art, cel shading, large expressive eyes, vivid skies, detailed painted backgrounds and soft light." },
  { id: "cartoon", label: "Cartoon",
    style: "Bright cartoon style: bold outlines, simple shapes, flat vibrant colours, playful exaggerated poses and expressions." },
  { id: "watercolour", label: "Watercolour storybook",
    style: "Hand-painted watercolour storybook illustration: soft washes, visible paper texture, delicate ink outlines, gentle pastel light." },
  { id: "comic", label: "Comic book",
    style: "Comic-book illustration: confident ink lines, halftone shading, strong shadows, saturated colours and dynamic framing." },
  { id: "clay", label: "Claymation",
    style: "Stop-motion claymation look: handmade clay characters with fingerprint texture, miniature sets, soft studio lighting, slight imperfections." },
  { id: "realistic", label: "Realistic film",
    style: "Realistic cinematic film still: natural proportions, true-to-life materials, soft natural lighting, shallow depth of field." },
  { id: "sketch", label: "Pencil sketch",
    style: "Pencil sketch storyboard: graphite lines, light cross-hatching, minimal grey tones on off-white paper." },
];

function StylePicker({ value, onChange, hasPictures }: { value: string; onChange: (s: string) => void; hasPictures: boolean }) {
  const current = STYLE_PRESETS.find((p) => p.style === value.trim())?.id ?? "custom";
  return (
    <div className="stack" style={{ gap: 6 }}>
      <label className="field">Visual style for every image
        <select value={current} style={{ width: "100%" }}
          onChange={(e) => { const p = STYLE_PRESETS.find((x) => x.id === e.target.value); if (p) onChange(p.style); }}>
          {STYLE_PRESETS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
          <option value="custom">{current === "custom" && value ? "Custom (from your PDF or typed)" : "Custom (type your own)"}</option>
        </select>
      </label>
      <AutoTextarea minRows={2} value={value} aria-label="Style description" placeholder="Describe the look, e.g. warm, hand-painted animation"
        onChange={(e) => onChange(e.target.value)} />
      {hasPictures && (
        <span className="faint small">Pictures already made keep their old style. To switch styles, use Start over on them after saving.</span>
      )}
    </div>
  );
}

/** Remove quote marks the PDF already put around a line, so the UI doesn't show them twice. */
function cleanDialogue(text: string) {
  return text.replace(/["“”]/g, "").replace(/\s+/g, " ").trim();
}
