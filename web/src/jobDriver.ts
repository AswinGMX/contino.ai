import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { api } from "./api";

/**
 * Runs a project's queued jobs from the open browser tab when the API has no separate
 * worker (serverless hosting such as Vercel).
 *
 * Each `/jobs/run` call is its own short-lived function invocation that claims one job
 * (database lock: a job is never run twice), runs it, and returns. A few loops run in
 * parallel, so a scene's shots are drawn side by side. The loops stop when the queue is
 * empty and start again when the project reports new active jobs. If the tab closes,
 * queued jobs simply wait until the project is opened again.
 */
export function useJobDriver(pid: string, activeJobs: number) {
  const qc = useQueryClient();
  const cfg = useQuery({ queryKey: ["config"], queryFn: api.config, staleTime: 5 * 60_000, retry: false });
  const enabled = !!cfg.data?.browser_runs_jobs;
  const parallel = Math.max(1, Math.min(cfg.data?.browser_parallel_jobs ?? 3, 6));
  const running = useRef(0);
  const current = useRef(pid);
  current.current = pid;

  useEffect(() => {
    if (!enabled || !pid || activeJobs <= 0) return;
    const loop = async () => {
      running.current += 1;
      try {
        while (current.current === pid) {
          let ran: string | null = null;
          try {
            ran = (await api.runJob(pid)).ran;
          } catch {
            await new Promise((r) => setTimeout(r, 3000)); // network blip or timeout: back off, retry on next poll
            break;
          }
          if (!ran) break; // nothing claimable right now
          qc.invalidateQueries(); // a job finished: refresh what is on screen
        }
      } finally {
        running.current -= 1;
      }
    };
    for (let i = running.current; i < Math.min(parallel, activeJobs); i++) void loop();
  }, [enabled, pid, activeJobs, parallel, qc]);
}
