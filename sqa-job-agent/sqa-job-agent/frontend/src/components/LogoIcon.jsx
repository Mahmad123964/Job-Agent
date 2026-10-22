// SQA Job Agent mark: a radar scan — continuous monitoring. Flat, single
// colour (currentColor) on a 24×24 grid, so it recolours with Tailwind
// text-* classes and scales from 16px (favicon) to 40px+.
//
// The sweep rotates and the contact blip pulses while `animated` is true;
// both stop under prefers-reduced-motion (global rule in index.css).
// public/favicon.svg is a static copy of the same geometry.

export function LogoRadar({ size = 24, animated = true, ...props }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      role="img"
      aria-label="SQA Job Agent"
      {...props}
    >
      <circle cx="12" cy="12" r="9.25" />
      <circle cx="12" cy="12" r="4.75" strokeOpacity="0.45" />
      <circle cx="12" cy="12" r="1.25" fill="currentColor" stroke="none" />
      {/* sweep: wedge + leading edge, rotating about the centre */}
      <g className={animated ? "animate-sweep" : undefined} style={{ transformOrigin: "12px 12px", transformBox: "view-box" }}>
        <path d="M12 12 L12 2.75 A9.25 9.25 0 0 1 20.0 7.4 Z" fill="currentColor" fillOpacity="0.22" stroke="none" />
        <path d="M12 12 L12 2.75" strokeWidth="2" />
      </g>
      {/* contact blip */}
      <circle cx="16.4" cy="8.2" r="1.35" fill="currentColor" stroke="none" />
      {animated && (
        <circle
          cx="16.4"
          cy="8.2"
          r="1.35"
          fill="none"
          strokeWidth="1"
          className="animate-pulseDot"
          style={{ transformOrigin: "16.4px 8.2px", transformBox: "view-box" }}
        />
      )}
    </svg>
  );
}

export default LogoRadar;
