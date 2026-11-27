import { Overview } from "./api";

export type StepKey = "plan" | "library" | "storyboard" | "finals" | "usage";

export interface StepInfo { key: StepKey; n: number; name: string; sub: string; done: boolean }

export const money2 = (n: number) => `$${n.toFixed(2)}`;

export function steps(o: Overview | undefined): StepInfo[] {
  const c = o?.counts;
  const planOk = o?.plan_status === "approved";
  const parse = o?.project.parse_status;
  const libDone = !!c && c.library_total > 0 && c.library_approved === c.library_total;
  const boardsDone = !!c && c.scenes > 0 && c.boards_approved === c.scenes;
  const finalsDone = !!c && c.shots > 0 && c.finals_approved === c.shots;
  return [
    { key: "plan", n: 1, name: "Plan", done: planOk,
      sub: planOk ? "Done" : parse === "parsing" ? "Reading your PDF…" : parse === "failed" ? "Couldn’t read the PDF" : "Check & approve" },
    { key: "library", n: 2, name: "Characters & places", done: libDone,
      sub: !planOk ? "After the plan" : libDone ? "Done" : `${c?.library_approved ?? 0} of ${c?.library_total ?? 0} approved` },
    { key: "storyboard", n: 3, name: "Storyboard", done: boardsDone,
      sub: boardsDone ? "Done" : `${c?.boards_approved ?? 0} of ${c?.scenes ?? 0} scenes approved` },
    { key: "finals", n: 4, name: "Final images", done: finalsDone,
      sub: finalsDone ? "Done" : `${c?.finals_approved ?? 0} of ${c?.shots ?? 0} ready` },
    { key: "usage", n: 5, name: "Cost", done: false, sub: `${money2(o?.cost_usd ?? 0)} so far` },
  ];
}

/** Where the project is now, for the Home "continue" card. */
export function progress(o: Overview) {
  const st = steps(o);
  const c = o.counts;
  const current = st.find((s) => !s.done && s.key !== "usage") ?? st[4];
  const frac = (a: number, b: number) => (b ? a / b : 0);
  const pct = Math.round(100 * (
    (st[0].done ? 1 : 0) + frac(c.library_approved, c.library_total) + frac(c.boards_approved, c.scenes) + frac(c.finals_approved, c.shots)
  ) / 4);
  const next: Record<StepKey, string> = {
    plan: o.project.parse_status === "parsing" ? "We’re reading your Shot Planner. This takes a minute."
      : o.project.parse_status === "failed" ? "We couldn’t read the PDF. Open the project to try again."
      : "Check the shots we read from your PDF, then approve the plan.",
    library: `${c.library_total - c.library_approved} picture${c.library_total - c.library_approved === 1 ? "" : "s"} of characters and places still need your OK.`,
    storyboard: `${c.scenes - c.boards_approved} scene${c.scenes - c.boards_approved === 1 ? "" : "s"} still to review in the storyboard.`,
    finals: `${c.shots - c.finals_approved} final image${c.shots - c.finals_approved === 1 ? "" : "s"} still to make or approve.`,
    usage: "Everything is approved. Download your images and cost log.",
  };
  const cta: Record<StepKey, string> = {
    plan: "Check the plan", library: "Review characters & places", storyboard: "Continue reviewing",
    finals: "Go to final images", usage: "Download everything",
  };
  return { step: current, pct, next: next[current.key], cta: cta[current.key] };
}
