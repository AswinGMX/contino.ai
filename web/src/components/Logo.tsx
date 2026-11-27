// contino.ai brand: mark redrawn as SVG (sharp at any size) from web/public/contino-ai-light.png.

export function LogoMark({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 320 320" role="img" aria-label="contino.ai" style={{ flex: "none", display: "block" }}>
      <rect width="320" height="320" rx="72" fill="#0E1A36" />
      <rect x="48" y="49" width="164" height="115" rx="17" fill="none" stroke="#8FB0FF" strokeWidth="13" />
      <rect x="81" y="84" width="164" height="115" rx="17" fill="#0E1A36" stroke="#8FB0FF" strokeWidth="13" />
      <rect x="108" y="122" width="168" height="124" rx="18" fill="#2E6BFF" />
      <circle cx="156" cy="203" r="16" fill="#FFFFFF" />
    </svg>
  );
}

export function Wordmark({ size = 17 }: { size?: number }) {
  return (
    <span className="wordmark" style={{ fontSize: size }}>
      contino<span className="wordmark-ai">.ai</span>
    </span>
  );
}

export function Logo({ size = 30, tagline }: { size?: number; tagline?: boolean }) {
  return (
    <span className="logo">
      <LogoMark size={size} />
      <span className="vstack" style={{ gap: 0, lineHeight: 1.1 }}>
        <Wordmark size={size * 0.58} />
        {tagline && <span className="logo-tagline">Same cast. Every frame.</span>}
      </span>
    </span>
  );
}

export const TAGLINE = "Same cast. Every frame.";
