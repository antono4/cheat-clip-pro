import React from 'react';
import { useLanguage } from '../locales';
import { useTheme } from '../theme';

const SunIcon: React.FC = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
    <circle cx="12" cy="12" r="4.2" />
    <path d="M12 2.2v2.4M12 19.4v2.4M2.2 12h2.4M19.4 12h2.4M5.1 5.1l1.7 1.7M17.2 17.2l1.7 1.7M18.9 5.1l-1.7 1.7M6.8 17.2l-1.7 1.7" />
  </svg>
);

const MoonIcon: React.FC = () => (
  <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d="M21.2 13.1A8.7 8.7 0 0 1 10.9 2.8a1 1 0 0 0-1.3 1.3 7 7 0 0 0 9.3 9.3 1 1 0 0 0 1.3-1.3Z" />
  </svg>
);

export const ThemeSwitcher: React.FC = () => {
  const { theme, setTheme } = useTheme();
  const { t } = useLanguage();

  const base: React.CSSProperties = {
    display: 'inline-flex',
    alignItems: 'center',
    gap: '6px',
    padding: '6px 10px',
    fontSize: '0.75rem',
    fontWeight: 600,
    border: 'none',
    borderRadius: '16px',
    minHeight: '24px',
    cursor: 'pointer',
    transition: 'all 0.2s ease',
  };

  const isLight = theme === 'light';

  return (
    <div
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        padding: '3px',
        borderRadius: '20px',
        background: 'rgba(var(--tint), 0.04)',
        border: '1px solid var(--border-color)',
        backdropFilter: 'blur(8px)',
        position: 'relative',
        userSelect: 'none',
      }}
      role="group"
      aria-label={t.theme.label}
    >
      <button
        type="button"
        onClick={() => setTheme('dark')}
        aria-pressed={!isLight}
        style={{
          ...base,
          background: !isLight ? 'linear-gradient(135deg, var(--primary) 0%, var(--secondary) 100%)' : 'transparent',
          color: !isLight ? 'var(--on-primary)' : 'var(--text-secondary)',
          boxShadow: !isLight ? '0 2px 8px rgba(16, 185, 129, 0.35)' : 'none',
        }}
        title={t.theme.dark}
      >
        <MoonIcon />
        <span>{t.theme.darkShort}</span>
      </button>

      <button
        type="button"
        onClick={() => setTheme('light')}
        aria-pressed={isLight}
        style={{
          ...base,
          background: isLight ? 'linear-gradient(135deg, var(--primary) 0%, var(--secondary) 100%)' : 'transparent',
          color: isLight ? 'var(--on-primary)' : 'var(--text-secondary)',
          boxShadow: isLight ? '0 2px 8px rgba(16, 185, 129, 0.35)' : 'none',
        }}
        title={t.theme.light}
      >
        <SunIcon />
        <span>{t.theme.lightShort}</span>
      </button>
    </div>
  );
};
