import { createContext } from 'react';
import type { Translations } from './en';

export type Language = 'en' | 'id';

export interface LanguageContextType {
  language: Language;
  setLanguage: (lang: Language) => void;
  t: Translations;
}

const STORAGE_KEY = 'cheat_clip_language';

/**
 * Reads the saved language, falling back to the browser's preference.
 * Exported so the provider and tests can share one implementation.
 */
export const getInitialLanguage = (): Language => {
  if (typeof window === 'undefined') return 'en';

  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved === 'en' || saved === 'id') {
    return saved;
  }

  const browserLang = (navigator.language || '').toLowerCase();
  if (browserLang.startsWith('id')) {
    return 'id';
  }
  return 'en';
};

export const LANGUAGE_STORAGE_KEY = STORAGE_KEY;

export const LanguageContext = createContext<LanguageContextType | undefined>(undefined);
