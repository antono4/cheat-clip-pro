import React, { useState, useEffect, useMemo } from 'react';
import { en, type Translations } from './en';
import { id } from './id';
import {
  LanguageContext,
  LANGUAGE_STORAGE_KEY,
  getInitialLanguage,
  type Language,
} from './languageContext';

/**
 * Owns the active language. Only components live here; the context object and
 * the initial-language helper sit in `languageContext.ts` so fast refresh can
 * still swap this file in place.
 */
export const LanguageProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [language, setLanguageState] = useState<Language>(getInitialLanguage);

  const setLanguage = (newLang: Language) => {
    setLanguageState(newLang);
    localStorage.setItem(LANGUAGE_STORAGE_KEY, newLang);
  };

  // Sync state if another tab updates the preference
  useEffect(() => {
    const handleStorageChange = (e: StorageEvent) => {
      if (e.key === LANGUAGE_STORAGE_KEY && (e.newValue === 'en' || e.newValue === 'id')) {
        setLanguageState(e.newValue);
      }
    };
    window.addEventListener('storage', handleStorageChange);
    return () => window.removeEventListener('storage', handleStorageChange);
  }, []);

  const t: Translations = useMemo(() => (language === 'id' ? id : en), [language]);

  return (
    <LanguageContext.Provider value={{ language, setLanguage, t }}>
      {children}
    </LanguageContext.Provider>
  );
};
