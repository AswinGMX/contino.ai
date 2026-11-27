import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, num, usd } from "../api";
import { Icon } from "../components/icons";
import { ActionButton, Loading, money, NextBar, useToast } from "../components/ui";

const STEP_NAMES: Record<string, string> = {
  final: "Final images", panel: "Storyboard sketches", panel_fix: "Shot fixes", review: "AI checks",
  character_ref: "Character pictures", reaction_ref: "Expressions", reaction_sheet: "Expression sheets",
  location_ref: "Place pictures", parse: "Reading the PDF",
};

export default function UsagePage({ pid }: { pid: string }) {
  const toast = useToast();
  const q = useQuery({ queryKey: ["usage", pid], queryFn: () => api.usage(pid), refetchInterval: 5000 });
  const [log, setLog] = useState(false);
  const [allCalls, setAllCalls] = useState(false);
  if (q.isLoading || !q.data) return <Loading />;
  const u = q.data;
  const t = u.totals;
  const b = u.budget;
  const steps = Object.entries(u.by_step).sort((a, z) => z[1].cost_usd - a[1].cost_usd);
  const max = Math.max(...steps.map(([, v]) => v.cost_usd), 0.0001);
  const retried = steps.filter(([, v]) => v.failed > 0).sort((a, z) => z[1].failed - a[1].failed);
  const calls = allCalls ? u.calls : u.calls.slice(0, 40);

  return (
    <>
      <NextBar step={5} tone={t.approved_finals ? "ok" : "todo"} icon="download"
        title={t.approved_finals ? "Download your results" : "Cost so far"}
        text={t.approved_finals
          ? "Get every approved image, the storyboards and review notes as a .zip, plus the cost log as a CSV."
          : "Every AI request is logged with real token counts. Downloads unlock once final images are approved."}>
        <a className="btn" href={api.usageCsvUrl(pid)}><Icon name="download" size={15} />Cost log (CSV)</a>
        <a className={`btn btn-primary${t.approved_finals ? "" : " disabled"}`} href={api.exportZipUrl(pid)}><Icon name="download" size={15} />All outputs (.zip)</a>
      </NextBar>

      <section className="card" style={{ display: "flex", flexWrap: "wrap" }}>
        <div className="stat"><span className="stat-label">Spent on this project</span><span className="stat-value">${t.cost_usd.toFixed(2)}</span>
          <span className="faint small">{num(t.calls)} AI requests{t.failed_calls ? ` · ${t.failed_calls} failed` : ""}</span></div>
        <div className="stat" style={{ borderLeft: "1px solid var(--line)" }}><span className="stat-label">Cost per approved image</span>
          <span className="stat-value">{money(t.cost_per_approved_final_usd)}</span>
          <span className="faint small">{t.approved_finals ? `${t.approved_finals} images · includes retries and shared costs` : "No approved images yet"}</span></div>
        <div className="stat" style={{ borderLeft: "1px solid var(--line)" }}><span className="stat-label">Budget left (all projects)</span>
          <span className="stat-value">${b.remaining_usd.toFixed(2)}</span>
          <span className="faint small">of ${b.limit_usd.toFixed(0)}{b.blocked ? " · paused" : b.warn ? " · running low" : ""}</span></div>
      </section>

      <div className="split">
        <section className="card card-pad stack">
          <h3 className="section-title">Where the money went</h3>
          {steps.length === 0 ? <p className="muted">Nothing spent yet.</p> : (
            <ul className="list stack" style={{ gap: 10 }}>
              {steps.map(([k, v]) => (
                <li key={k} className="money-row">
                  <span>{STEP_NAMES[k] ?? k}</span>
                  <span className="track" aria-hidden><span style={{ width: `${Math.max(2, (100 * v.cost_usd) / max)}%` }} /></span>
                  <span className="amt">${v.cost_usd.toFixed(2)}</span>
                </li>
              ))}
            </ul>
          )}
          <button className="btn btn-text btn-sm" style={{ alignSelf: "flex-start" }} onClick={() => setLog(!log)} aria-expanded={log}>
            {log ? "Hide details" : "Show cost per shot and every request"}<Icon name="chevronDown" size={14} style={{ transform: log ? "rotate(180deg)" : undefined }} />
          </button>
        </section>
        <section className="card card-pad stack" style={{ background: "var(--warn-soft)", borderColor: "transparent" }}>
          <b className="hstack" style={{ gap: 6, color: "var(--warn-ink)" }}><Icon name="lightbulb" size={16} />Tip to save money</b>
          <p className="small" style={{ color: "var(--text-2)" }}>
            {retried.length
              ? `${STEP_NAMES[retried[0][0]] ?? retried[0][0]} had to retry ${retried[0][1].failed} time${retried[0][1].failed > 1 ? "s" : ""}. Failed attempts are usually network drops, so a steady connection saves money.`
              : "Fix single shots instead of redrawing a whole scene, and approve sketches before making finals. Finals cost the most."}
          </p>
          <ActionButton kind="text" size="sm" icon="folder" onClick={async () => { const r = await api.exportOutputs(pid); toast.ok(`Saved to ${r.path}`); }}>Save outputs to the output/ folder</ActionButton>
        </section>
      </div>

      {log && (
        <>
          <section className="card">
            <div className="card-head"><h3 className="section-title">Cost per shot</h3><span className="faint small">Shared costs are split evenly across shots</span></div>
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Shot</th><th className="r">Requests</th><th className="r">Images</th><th className="r">Own</th><th className="r">Shared</th><th className="r">Total</th></tr></thead>
                <tbody>
                  {u.by_shot.map((s) => (
                    <tr key={s.shot_id}><td>{s.shot_id}</td><td className="r">{s.calls}</td><td className="r">{s.images}</td>
                      <td className="r">{usd(s.direct_cost_usd)}</td><td className="r">{usd(s.shared_cost_usd)}</td><td className="r"><b>{usd(s.total_cost_usd)}</b></td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
          <section className="card">
            <div className="card-head"><h3 className="section-title">Every request</h3><span className="faint small">Newest first</span></div>
            <div className="table-wrap">
              <table className="table">
                <thead><tr><th>Time</th><th>Shot</th><th>Step</th><th>Model</th><th className="r">Tokens in</th><th className="r">Tokens out</th><th className="r">Cost</th><th>Result</th></tr></thead>
                <tbody>
                  {calls.map((c) => (
                    <tr key={c.id} className={c.ok ? "" : "row-fail"}>
                      <td className="tnum">{new Date(c.time).toLocaleTimeString()}</td><td>{c.shot_id}</td><td>{STEP_NAMES[c.step] ?? c.step}</td>
                      <td className="faint">{c.model}</td><td className="r">{num(c.input_tokens)}</td><td className="r">{num(c.output_tokens)}</td>
                      <td className="r">{usd(c.cost_usd)}</td><td title={c.error}>{c.ok ? "OK" : `Failed (try ${c.attempt})`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {u.calls.length > 40 && <div style={{ padding: 8 }}><button className="btn btn-text btn-sm" onClick={() => setAllCalls(!allCalls)}>{allCalls ? "Show fewer" : `Show all ${u.calls.length}`}</button></div>}
          </section>
        </>
      )}
    </>
  );
}
