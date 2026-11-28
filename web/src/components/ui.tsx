import { createContext, ReactNode, TextareaHTMLAttributes, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ApiError, Budget, Status } from "../api";
import { Icon, IconName } from "./icons";

// ------------------------------------------------------------------ formatting

export const money = (n: number | null | undefined) =>
  n == null ? "—" : n < 1 ? `${Math.max(1, Math.round(n * 100))}¢` : `$${n.toFixed(2)}`;

const AV_COLORS = ["#7A5AF8", "#2563EB", "#0E9F6E", "#D97706", "#DB2777", "#C2410C", "#0891B2", "#4F46E5"];
export function avatarColor(seed: string) {
  let h = 0;
  for (const ch of seed) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return AV_COLORS[h % AV_COLORS.length];
}

export function Avatar({ name, src, size = 32 }: { name: string; src?: string | null; size?: number }) {
  return (
    <span className="av" style={{ width: size, height: size, background: src ? "var(--surface-3)" : avatarColor(name), fontSize: size * 0.4 }}>
      {src ? <img src={src} alt="" /> : name.replace(/^the\s+/i, "").charAt(0).toUpperCase()}
    </span>
  );
}

// ------------------------------------------------------------------ status

const STATUS: Record<Status, { label: string; cls: string; icon?: IconName; busy?: boolean }> = {
  pending: { label: "Not made yet", cls: "p-mute" },
  generating: { label: "Making…", cls: "p-info", busy: true },
  in_review: { label: "AI checking…", cls: "p-info", busy: true },
  needs_approval: { label: "Check & approve", cls: "p-warn" },
  approved: { label: "Approved", cls: "p-ok", icon: "check" },
  rejected: { label: "Redoing…", cls: "p-info", busy: true },
  stale: { label: "Out of date", cls: "p-warn" },
  failed: { label: "Failed", cls: "p-err", icon: "alert" },
};

export function Pill({ cls, children, icon, busy }: { cls: string; children: ReactNode; icon?: IconName; busy?: boolean }) {
  return (
    <span className={`pill ${cls}`}>
      {busy && <span className="pulse" aria-hidden />}
      {icon && <Icon name={icon} size={12} strokeWidth={2.8} />}
      {children}
    </span>
  );
}

export function StatusPill({ status, label }: { status: Status | null | undefined; label?: string }) {
  const s = STATUS[status ?? "pending"];
  return <Pill cls={s.cls} icon={s.icon} busy={s.busy}>{label ?? s.label}</Pill>;
}

export function OkDot({ size = 20 }: { size?: number }) {
  return <span className="dot dot-done" style={{ width: size, height: size }}><Icon name="check" size={size * 0.6} strokeWidth={3} /></span>;
}

// ------------------------------------------------------------------ the "what to do now" bar

export function NextBar({ step, tone = "todo", icon, title, text, progress, children }: {
  step?: number; tone?: "todo" | "ok" | "warn" | "wait"; icon: IconName; title: ReactNode; text?: ReactNode;
  progress?: { done: number; total: number; label?: string }; children?: ReactNode;
}) {
  const kicker = tone === "ok" ? "Step done" : tone === "wait" ? "Please wait" : "What to do now";
  return (
    <section className={`next${tone === "todo" ? "" : ` next-${tone}`}`} aria-live="polite">
      <span className="next-icon">{tone === "wait" ? <span className="spinner" /> : <Icon name={icon} size={18} />}</span>
      <div className="next-body">
        <span className="next-kicker">{step ? `Step ${step} of 5 · ` : ""}{kicker}</span>
        <span className="next-title">{title}</span>
        {text && <span className="next-text">{text}</span>}
      </div>
      {progress && progress.total > 0 && (
        <div className="next-progress">
          <span className={`bar ${progress.done === progress.total ? "bar-ok" : ""}`} style={{ width: 90 }}>
            <span style={{ width: `${(100 * progress.done) / progress.total}%` }} />
          </span>
          <span><b style={{ color: "var(--text)" }}>{progress.done}/{progress.total}</b> {progress.label ?? "done"}</span>
        </div>
      )}
      {children && <div className="next-actions">{children}</div>}
    </section>
  );
}

// ------------------------------------------------------------------ toasts

type Toast = { id: number; kind: "error" | "ok" | "info"; text: string };
const ToastCtx = createContext<(kind: Toast["kind"], text: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const seq = useRef(0);
  const push = useCallback((kind: Toast["kind"], text: string) => {
    const id = ++seq.current;
    setToasts((t) => [...t.slice(-3), { id, kind, text }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), kind === "error" ? 7000 : 3000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`} onClick={() => setToasts((x) => x.filter((y) => y.id !== t.id))}>{t.text}</div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function useToast() {
  const push = useContext(ToastCtx);
  return {
    ok: (t: string) => push("ok", t),
    info: (t: string) => push("info", t),
    error: (e: unknown) => push("error", e instanceof ApiError || e instanceof Error ? e.message : String(e)),
  };
}

// ------------------------------------------------------------------ buttons

type BtnKind = "primary" | "secondary" | "success" | "soft" | "danger" | "text";
const btnCls = (kind: BtnKind, size?: "sm", block?: boolean, iconOnly?: boolean) =>
  `btn${kind === "secondary" ? "" : ` btn-${kind}`}${size ? " btn-sm" : ""}${block ? " btn-block" : ""}${iconOnly ? " btn-icon" : ""}`;

export function ActionButton({ onClick, children, kind = "secondary", size, block, icon, iconRight, disabled, title, label }: {
  onClick: () => Promise<unknown> | void; children?: ReactNode; kind?: BtnKind; size?: "sm"; block?: boolean;
  icon?: IconName; iconRight?: IconName; disabled?: boolean; title?: string; label?: string;
}) {
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  return (
    <button className={btnCls(kind, size, block, !children)} disabled={disabled || busy} title={title ?? label} aria-label={label}
      onClick={async () => { setBusy(true); try { await onClick(); } catch (e) { toast.error(e); } finally { setBusy(false); } }}>
      {busy ? <span className="spinner" aria-hidden /> : icon && <Icon name={icon} size={15} />}
      {children}
      {iconRight && !busy && <Icon name={iconRight} size={15} />}
    </button>
  );
}

/** A button that turns into a one-line input with a submit button. */
export function CommentAction({ label, placeholder, onSubmit, kind = "secondary", size = "sm", icon, initial = "",
  submitLabel, optional, disabled, block, hint }: {
  label: string; placeholder: string; onSubmit: (comment: string) => Promise<unknown>; kind?: BtnKind; size?: "sm" | undefined;
  icon?: IconName; initial?: string; submitLabel?: string; optional?: boolean; disabled?: boolean; block?: boolean; hint?: string;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState(initial);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { if (open) { setText((t) => t || initial); ref.current?.focus(); } }, [open, initial]);

  if (!open) {
    return (
      <button className={btnCls(kind, size, block)} disabled={disabled} onClick={() => setOpen(true)}>
        {icon && <Icon name={icon} size={15} />}{label}
      </button>
    );
  }
  const ready = optional || !!text.trim();
  const submit = async () => {
    if (!ready) return;
    setBusy(true);
    try { await onSubmit(text.trim()); setOpen(false); setText(""); } catch (e) { toast.error(e); } finally { setBusy(false); }
  };
  return (
    <form className="comment-form" onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <input ref={ref} value={text} maxLength={300} placeholder={placeholder} aria-label={placeholder}
        onChange={(e) => setText(e.target.value)} onKeyDown={(e) => e.key === "Escape" && setOpen(false)} />
      <button type="submit" className="btn btn-primary btn-sm" disabled={busy || !ready}>
        {busy && <span className="spinner" aria-hidden />}{submitLabel ?? label}
      </button>
      <button type="button" className="btn btn-text btn-sm" onClick={() => setOpen(false)}>Cancel</button>
      {hint && <span className="comment-hint">{hint}</span>}
    </form>
  );
}

/** Hidden file input behind a button. */
export function UploadButton({ label, accept, onFile, kind = "secondary", size = "sm", icon = "upload", iconOnly, block }: {
  label: string; accept: string; onFile: (f: File) => Promise<unknown>; kind?: BtnKind; size?: "sm" | undefined;
  icon?: IconName; iconOnly?: boolean; block?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  return (
    <>
      <input ref={input} type="file" accept={accept} hidden onChange={async (e) => {
        const f = e.target.files?.[0];
        e.target.value = "";
        if (!f) return;
        setBusy(true);
        try { await onFile(f); } catch (err) { toast.error(err); } finally { setBusy(false); }
      }} />
      <button className={btnCls(kind, size, block, iconOnly)} disabled={busy} title={label} aria-label={iconOnly ? label : undefined}
        onClick={() => input.current?.click()}>
        {busy ? <span className="spinner" aria-hidden /> : <Icon name={icon} size={15} />}{!iconOnly && label}
      </button>
    </>
  );
}

// ------------------------------------------------------------------ images

const LightboxCtx = createContext<(src: string, caption?: string) => void>(() => {});
export const useLightbox = () => useContext(LightboxCtx);

type Slide = { src: string; caption: string };

export function LightboxProvider({ children }: { children: ReactNode }) {
  const [slides, setSlides] = useState<Slide[]>([]);
  const [index, setIndex] = useState(-1);
  const open = index >= 0 && slides[index];

  const show = useCallback((src: string, caption?: string) => {
    // Gallery = every zoomable image on the page, in page order.
    const seen = new Set<string>();
    const list: Slide[] = [];
    document.querySelectorAll<HTMLImageElement>("img[data-lightbox]").forEach((el) => {
      const s = el.getAttribute("src");
      if (s && !seen.has(s)) { seen.add(s); list.push({ src: s, caption: el.getAttribute("alt") ?? "" }); }
    });
    let i = list.findIndex((x) => x.src === src);
    if (i < 0) { list.splice(0, list.length, { src, caption: caption ?? "" }); i = 0; }
    setSlides(list);
    setIndex(i);
  }, []);

  const close = () => setIndex(-1);
  const step = useCallback((d: number) => setIndex((i) => (i < 0 ? i : (i + d + slides.length) % slides.length)), [slides.length]);

  useEffect(() => {
    if (index < 0) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
      else if (e.key === "ArrowRight") { e.preventDefault(); step(1); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); step(-1); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, step]);

  return (
    <LightboxCtx.Provider value={show}>
      {children}
      {open && createPortal(
        <div className="lightbox" onClick={close} role="dialog" aria-modal aria-label="Image viewer">
          <button className="lb-btn lb-close" aria-label="Close" onClick={(e) => { e.stopPropagation(); close(); }}><Icon name="x" size={20} /></button>
          {slides.length > 1 && (
            <button className="lb-btn lb-prev" aria-label="Previous image" onClick={(e) => { e.stopPropagation(); step(-1); }}><Icon name="arrowLeft" size={22} /></button>
          )}
          <img key={open.src} src={open.src} alt={open.caption} onClick={(e) => e.stopPropagation()} />
          {slides.length > 1 && (
            <button className="lb-btn lb-next" aria-label="Next image" onClick={(e) => { e.stopPropagation(); step(1); }}><Icon name="arrowRight" size={22} /></button>
          )}
          <div className="lightbox-caption" onClick={(e) => e.stopPropagation()}>
            {open.caption}{slides.length > 1 && <span className="lb-count">{index + 1} / {slides.length}</span>}
          </div>
        </div>, document.body,
      )}
    </LightboxCtx.Provider>
  );
}

export function Thumb({ src, alt, status, aspect = "16 / 9", empty = "Not made yet", className = "", position, badge, dim }: {
  src: string | null | undefined; alt: string; status?: Status; aspect?: string; empty?: string; className?: string;
  position?: string; badge?: ReactNode; dim?: boolean;
}) {
  const open = useLightbox();
  const busy = status === "generating" || status === "rejected";
  return (
    <div className={`thumb ${className}`} style={{ aspectRatio: aspect }}>
      {src ? (
        <img src={src} alt={alt} loading="lazy" data-lightbox="" style={{ objectPosition: position, opacity: dim ? 0.5 : 1 }} onClick={() => open(src, alt)} />
      ) : (
        <div className="thumb-empty">{busy ? <><span className="spinner" />Making it…</> : <><Icon name="image" size={18} />{empty}</>}</div>
      )}
      {src && busy && <div className="thumb-overlay"><span className="spinner" />Updating…</div>}
      {badge && <div className="thumb-badge">{badge}</div>}
    </div>
  );
}

// ------------------------------------------------------------------ layout bits

export function Tabs<T extends string>({ value, onChange, items, label }: {
  value: T; onChange: (v: T) => void; items: { value: T; label: ReactNode; n?: number }[]; label: string;
}) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {items.map((it) => (
        <button key={it.value} role="tab" aria-selected={value === it.value} className={`tab${value === it.value ? " on" : ""}`} onClick={() => onChange(it.value)}>
          {it.label}{it.n != null && <span className="n">{it.n}</span>}
        </button>
      ))}
    </div>
  );
}

export function Empty({ title, children, icon = "image" }: { title: string; children?: ReactNode; icon?: IconName }) {
  return <div className="empty"><span className="icon-tile"><Icon name={icon} size={18} /></span><h3>{title}</h3>{children}</div>;
}

export function Loading() {
  return <div className="empty"><span className="spinner" /> Loading…</div>;
}

export function BudgetCard({ budget }: { budget?: Budget }) {
  if (!budget) return null;
  const pct = Math.min(100, (budget.spent_usd / budget.limit_usd) * 100);
  const tone = budget.blocked ? "bar-err" : budget.warn ? "bar-warn" : "bar-ok";
  return (
    <div className="budget-card">
      <div className="row" style={{ gap: 6 }} title="Everything spent on the team's OpenAI key: all projects, all users">
        <b>Team budget</b><span className="tnum">${budget.spent_usd.toFixed(2)} / ${budget.limit_usd.toFixed(0)}</span>
      </div>
      <div className={`bar ${tone}`}><span style={{ width: `${Math.max(pct, 2)}%` }} /></div>
      <span className="faint">{budget.blocked ? "Limit reached · paused" : budget.warn ? "Over 80% used" : `$${budget.remaining_usd.toFixed(2)} left`} · all projects</span>
    </div>
  );
}

/** A textarea that grows to fit its text, so nothing is hidden behind a scrollbar. */
export function AutoTextarea({ minRows = 2, maxRows = 14, value, ...rest }:
  TextareaHTMLAttributes<HTMLTextAreaElement> & { minRows?: number; maxRows?: number; value: string }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const line = parseFloat(getComputedStyle(el).lineHeight) || 20;
    const pad = el.offsetHeight - el.clientHeight + parseFloat(getComputedStyle(el).paddingTop) + parseFloat(getComputedStyle(el).paddingBottom);
    el.style.height = "auto";
    const h = Math.min(Math.max(el.scrollHeight + (el.offsetHeight - el.clientHeight), minRows * line + pad), maxRows * line + pad);
    el.style.height = `${h}px`;
    el.style.overflowY = el.scrollHeight + (el.offsetHeight - el.clientHeight) > h ? "auto" : "hidden";
  }, [value, minRows, maxRows]);
  return <textarea ref={ref} rows={minRows} value={value} {...rest} />;
}

/** Renders children at the end of <body>, above every sticky/scrolling container. */
export function Portal({ children }: { children: ReactNode }) {
  return createPortal(children, document.body);
}
