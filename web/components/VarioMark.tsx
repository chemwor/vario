/** Vario mark: a variometer dial in three ink strokes (bezel, scale ticks with a long zero at 9 o'clock,
    needle swung up into the climb), one color via currentColor. */
export function VarioMark({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      className={className}
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="16" cy="16" r="12.5" />
      <path d="M3.5 16 H7.4 M7.2 7.2 L8.9 8.9 M7.2 24.8 L8.9 23.1 M16 3.5 V6 M16 28.5 V26 M24.8 7.2 L23.6 8.4 M24.8 24.8 L23.6 23.6" />
      <path d="M18.6 17.5 L8.4 11.6 M16 16 m-1.3 0 a1.3 1.3 0 1 0 2.6 0 a1.3 1.3 0 1 0 -2.6 0" />
    </svg>
  );
}
