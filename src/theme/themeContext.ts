import { createContext } from 'react';

export type Theme = 'dark' | 'light';

export interface ThemeContextType {
  theme: Theme;
  setTheme: (theme: Theme) => void;
  toggleTheme: () => void;
}

export const THEME_STORAGE_KEY = 'cheat_clip_theme';

/**
 * Reads the saved theme, falling back to the OS preference.
 * The app is dark-first, so dark wins unless the user chose otherwise.
 */
export const getInitialTheme = (): Theme => {
  if (typeof window === 'undefined') return 'dark';

  const saved = localStorage.getItem(THEME_STORAGE_KEY);
  if (saved === 'dark' || saved === 'light') {
    return saved;
  }
  if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) {
    return 'light';
  }
  return 'dark';
};

export const applyTheme = (theme: Theme) => {
  document.documentElement.setAttribute('data-theme', theme);
};

export const ThemeContext = createContext<ThemeContextType | undefined>(undefined);
