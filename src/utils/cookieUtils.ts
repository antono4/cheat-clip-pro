/**
 * Utility functions for managing YouTube cookies in client-side localStorage.
 * Ensures zero persistent storage of sensitive user cookies on the server.
 */

export const COOKIES_STORAGE_KEY = 'cheatclip_youtube_cookies';
const LEGACY_STORAGE_KEY = 'youtube_cookies';

/**
 * Normalizes raw cookie string (Netscape or JSON format) into standard Netscape format.
 */
export function normalizeCookies(rawContent: string): string {
  if (!rawContent) return '';

  // 1. Clean BOM and null bytes
  const text = rawContent
    .replace(/\ufeff/g, '')
    .replace(/\ufffe/g, '')
    .replace(/\x00/g, '')
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .trim();

  if (!text) return '';

  // 2. Check for JSON format (e.g. from Cookie-Editor / EditThisCookie)
  if (text.startsWith('[') || text.startsWith('{')) {
    try {
      let parsed = JSON.parse(text);
      if (!Array.isArray(parsed)) {
        parsed = [parsed];
      }
      if (Array.isArray(parsed) && parsed.length > 0 && typeof parsed[0] === 'object') {
        const lines: string[] = [
          '# Netscape HTTP Cookie File',
          '# http://curl.haxx.se/rfc/cookie_spec.html',
          '# Converted automatically from JSON format by CheatClip Pro',
          '',
        ];
        let count = 0;
        for (const item of parsed) {
          const domain = String(item.domain || item.host || '').trim();
          if (!domain) continue;
          const flag = domain.startsWith('.') ? 'TRUE' : 'FALSE';
          const path = String(item.path || '/').trim();
          const secure = item.secure ? 'TRUE' : 'FALSE';
          let expInt = Math.floor(Number(item.expirationDate || item.expires || item.expiry || 0));
          if (isNaN(expInt) || expInt <= 0) {
            expInt = 2147483647; // 2038 fallback
          }
          const name = String(item.name || '').trim();
          const val = String(item.value || '').trim();
          if (name) {
            lines.push(`${domain}\t${flag}\t${path}\t${secure}\t${expInt}\t${name}\t${val}`);
            count++;
          }
        }
        if (count > 0) {
          return lines.join('\n') + '\n';
        }
      }
    } catch {
      // Fallback to text parsing
    }
  }

  // 3. Handle standard Netscape text: ensure header is present
  const lines = text.split('\n');
  const cleanedLines: string[] = [];
  let hasHeader = false;

  for (const line of lines) {
    const stripped = line.trim();
    if (!stripped) continue;
    if (stripped.includes('# Netscape HTTP Cookie File')) {
      hasHeader = true;
    }
    cleanedLines.push(stripped);
  }

  if (!hasHeader) {
    cleanedLines.unshift(
      '# Netscape HTTP Cookie File',
      '# http://curl.haxx.se/rfc/cookie_spec.html',
      ''
    );
  }

  return cleanedLines.join('\n') + '\n';
}

/**
 * Extracts unique sample domain names from cookie text for preview.
 */
export function getCookieDomainSamples(content: string, maxSamples = 6): string[] {
  if (!content) return [];
  const sampleDomains: string[] = [];
  const lines = content.split('\n');

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const parts = trimmed.split('\t');
    if (parts.length >= 1 && parts[0]) {
      const domain = parts[0].trim();
      if (domain && !sampleDomains.includes(domain)) {
        sampleDomains.push(domain);
        if (sampleDomains.length >= maxSamples) break;
      }
    }
  }

  return sampleDomains;
}

/**
 * Retrieves YouTube cookies from browser localStorage.
 */
export function getStoredCookies(): string {
  try {
    const stored = localStorage.getItem(COOKIES_STORAGE_KEY) || localStorage.getItem(LEGACY_STORAGE_KEY) || '';
    return stored.trim();
  } catch {
    return '';
  }
}

/**
 * Saves YouTube cookies to browser localStorage.
 */
export function setStoredCookies(content: string): void {
  try {
    const normalized = normalizeCookies(content);
    localStorage.setItem(COOKIES_STORAGE_KEY, normalized);
    localStorage.setItem(LEGACY_STORAGE_KEY, normalized);
  } catch (e) {
    console.error('Failed to write cookies to localStorage:', e);
  }
}

/**
 * Removes YouTube cookies from browser localStorage.
 */
export function removeStoredCookies(): void {
  try {
    localStorage.removeItem(COOKIES_STORAGE_KEY);
    localStorage.removeItem(LEGACY_STORAGE_KEY);
  } catch (e) {
    console.error('Failed to remove cookies from localStorage:', e);
  }
}

/**
 * Checks if user has valid stored YouTube cookies in localStorage.
 */
export function hasStoredCookies(): boolean {
  const c = getStoredCookies();
  return Boolean(c && c.length >= 10);
}

/**
 * Returns size in bytes of stored cookies.
 */
export function getStoredCookiesSize(): number {
  const c = getStoredCookies();
  return c ? new Blob([c]).size : 0;
}
