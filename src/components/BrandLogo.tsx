import { useId } from 'react';

/**
 * Cheat Clip Pro brand mark: a monogram "C" with a play triangle nested in its
 * opening. Drawn as an arc plus a triangle rather than a font glyph so it stays
 * legible down to the 16px favicon size.
 */
export default function BrandLogo({ size = 40 }: { size?: number }) {
  const gradientId = useId();
  return (
    <svg
      className="logo-mark"
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      role="img"
      aria-label="Cheat Clip Pro"
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="48" y2="48" gradientUnits="userSpaceOnUse">
          <stop stopColor="var(--primary)" />
          <stop offset="1" stopColor="var(--secondary)" />
        </linearGradient>
      </defs>
      <rect width="48" height="48" rx="12" fill={`url(#${gradientId})`} />
      <path
        d="M31.07 15.57A11 11 0 1 0 31.07 32.43"
        stroke="var(--on-primary)"
        strokeWidth="6"
        strokeLinecap="round"
        fill="none"
      />
      <path d="M21.5 18.5L29 24L21.5 29.5Z" fill="var(--on-primary)" />
    </svg>
  );
}
