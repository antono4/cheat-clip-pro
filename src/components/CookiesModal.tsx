import React, { useState, useEffect } from 'react';
import { useLanguage } from '../locales';
import {
  getStoredCookies,
  setStoredCookies,
  removeStoredCookies,
  hasStoredCookies,
  getStoredCookiesSize,
  getCookieDomainSamples,
  normalizeCookies,
} from '../utils/cookieUtils';

interface CookiesModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCookieStatusChange?: (hasCookies: boolean) => void;
}

export const CookiesModal: React.FC<CookiesModalProps> = ({
  isOpen,
  onClose,
  onCookieStatusChange,
}) => {
  const { t } = useLanguage();
  const [cookieText, setCookieText] = useState<string>('');
  const [isCookieActive, setIsCookieActive] = useState<boolean>(false);
  const [cookieSize, setCookieSize] = useState<number>(0);
  const [sampleLines, setSampleLines] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [message, setMessage] = useState<{ text: string; type: 'success' | 'error' | 'info' } | null>(null);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState<boolean>(false);

  const loadStatus = () => {
    const raw = getStoredCookies();
    const active = hasStoredCookies();
    const size = getStoredCookiesSize();
    const samples = getCookieDomainSamples(raw);

    setIsCookieActive(active);
    setCookieSize(size);
    setSampleLines(samples);
    setCookieText(raw);

    if (onCookieStatusChange) {
      onCookieStatusChange(active);
    }
  };

  useEffect(() => {
    if (isOpen) {
      loadStatus();
      setMessage(null);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const readFileContent = (file: File) => {
    const reader = new FileReader();
    reader.onload = (event) => {
      const buffer = event.target?.result as ArrayBuffer;
      if (!buffer) return;
      const bytes = new Uint8Array(buffer);
      let encoding = 'utf-8';
      if (bytes.length >= 2) {
        if (bytes[0] === 0xFF && bytes[1] === 0xFE) {
          encoding = 'utf-16le';
        } else if (bytes[0] === 0xFE && bytes[1] === 0xFF) {
          encoding = 'utf-16be';
        } else if (bytes[1] === 0x00) {
          encoding = 'utf-16le';
        }
      }
      try {
        const decoder = new TextDecoder(encoding);
        const content = decoder.decode(bytes).replace(/\ufeff/g, '').replace(/\0/g, '');
        setCookieText(content);
        setMessage({
          text: t.cookies.fileLoadedInfo(file.name, (content.length / 1024).toFixed(1)),
          type: 'info'
        });
      } catch (err: any) {
        setMessage({ text: `Failed to read file: ${err.message}`, type: 'error' });
      }
    };
    reader.readAsArrayBuffer(file);
  };

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) readFileContent(file);
  };

  const handleSave = () => {
    if (!cookieText.trim()) {
      setMessage({ text: t.cookies.emptyError, type: 'error' });
      return;
    }
    setIsLoading(true);
    setMessage(null);

    try {
      const normalized = normalizeCookies(cookieText);
      if (!normalized || normalized.trim().length < 10) {
        setMessage({ text: t.cookies.emptyError, type: 'error' });
        setIsLoading(false);
        return;
      }

      setStoredCookies(normalized);
      loadStatus();
      setMessage({ text: t.cookies.saveSuccess, type: 'success' });
    } catch (err: any) {
      setMessage({ text: err.message || t.cookies.saveFailed, type: 'error' });
    } finally {
      setIsLoading(false);
    }
  };

  const executeDeleteCookies = () => {
    setIsLoading(true);
    setMessage(null);
    try {
      removeStoredCookies();
      loadStatus();
      setMessage({ text: t.cookies.deletedSuccess, type: 'info' });
    } catch (err: any) {
      setMessage({ text: err.message || t.cookies.removeFailed, type: 'error' });
    } finally {
      setIsLoading(false);
      setShowDeleteConfirm(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="cookies-modal-card" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="studio-modal-header">
          <div className="studio-header-title">
            <div className="studio-icon-badge">🍪</div>
            <div>
              <div className="studio-title-row">
                <h2>{t.cookies.modalTitle}</h2>
                <span className={`status-pill ${isCookieActive ? 'active' : 'inactive'}`}>
                  {isCookieActive ? t.cookies.statusActive : t.cookies.statusInactive}
                </span>
              </div>
              <p className="studio-header-desc">
                {t.cookies.headerDesc}
              </p>
            </div>
          </div>
          <button className="studio-close-btn" onClick={onClose}>
            ✕
          </button>
        </div>

        {/* Scrollable Content Area */}
        <div className="cookies-modal-scrollable">
          {/* Status banner */}
          <div className="cookies-status-section">
            {isCookieActive ? (
              <div className="cookie-status-box active">
                <span className="status-icon">🛡️</span>
                <div className="status-info">
                  <strong>{t.cookies.installedTitle}</strong>
                  <p>
                    {t.cookies.installedDesc(cookieSize)}
                  </p>
                  {sampleLines.length > 0 && (
                    <details className="cookie-preview-details">
                      <summary>{t.cookies.viewDomains(sampleLines.length)}</summary>
                      <div className="cookie-preview-code">
                        {sampleLines.map((line, idx) => (
                          <div key={idx}>{line}</div>
                        ))}
                      </div>
                    </details>
                  )}
                </div>
                <button
                  type="button"
                  className="btn-danger-outline"
                  onClick={() => setShowDeleteConfirm(true)}
                  disabled={isLoading}
                  style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', whiteSpace: 'nowrap' }}
                >
                  {t.cookies.clearCookiesBtn}
                </button>
              </div>
            ) : (
              <div className="cookie-status-box warning">
                <span className="status-icon">⚠️</span>
                <div className="status-info">
                  <strong>{t.cookies.noCookiesTitle}</strong>
                  <p>
                    {t.cookies.noCookiesDesc}
                  </p>
                </div>
              </div>
            )}
          </div>

          {/* Separate Storage Security Notice */}
          <div className="cookies-storage-notice">
            <span className="cookies-notice-icon">🛡️</span>
            <span>{t.cookies.clearCookiesNotice}</span>
          </div>

          {message && (
            <div className={`cookie-alert-box alert-${message.type}`}>
              {message.text}
            </div>
          )}

          {/* Input & Upload */}
          <div className="cookies-body-section">
            <div className="cookies-upload-row">
              <label className="cookies-file-label">
                {t.cookies.chooseFile}
                <input
                  type="file"
                  accept=".txt,.json,text/plain,application/json"
                  onChange={handleFileUpload}
                  style={{ display: 'none' }}
                />
              </label>
              <span className="cookies-or-divider">{t.cookies.orPaste}</span>
            </div>

            <textarea
              className="cookies-textarea"
              rows={7}
              placeholder={`# Netscape HTTP Cookie File (.txt) or JSON format (.json):\n# http://curl.haxx.se/rfc/cookie_spec.html\n.youtube.com\tTRUE\t/\tTRUE\t1750000000\tSID\t...\n\n# Or paste JSON format from Cookie-Editor / EditThisCookie:\n[{"domain": ".youtube.com", "name": "SID", "value": "..."}]`}
              value={cookieText}
              onChange={(e) => setCookieText(e.target.value)}
              onDragOver={(e) => {
                e.preventDefault();
                e.stopPropagation();
              }}
              onDrop={(e) => {
                e.preventDefault();
                e.stopPropagation();
                const file = e.dataTransfer.files?.[0];
                if (file) readFileContent(file);
              }}
            />

            <div className="cookies-guide-card">
              <h4>{t.cookies.guideTitle}</h4>
              <ol>
                <li>
                  {t.cookies.guideStep1Prefix}
                  <strong>{t.cookies.guideStep1Name}</strong>
                  {t.cookies.guideStep1Suffix}
                </li>
                <li>
                  {t.cookies.guideStep2Prefix}
                  <a href="https://www.youtube.com" target="_blank" rel="noopener noreferrer">YouTube.com</a>
                  {t.cookies.guideStep2Suffix}
                </li>
                <li>
                  {t.cookies.guideStep3Prefix}
                  <strong>{t.cookies.guideStep3Export}</strong>
                  {t.cookies.guideStep3Suffix}
                </li>
              </ol>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="studio-modal-footer">
          <button className="studio-btn-cancel" onClick={onClose}>
            {t.cookies.closeBtn}
          </button>
          <button
            className="studio-btn-render glowing-btn"
            onClick={handleSave}
            disabled={isLoading || !cookieText.trim()}
          >
            {isLoading ? t.cookies.savingBtn : t.cookies.saveBtn}
          </button>
        </div>

        {/* Clear Cookies Confirmation Modal */}
        {showDeleteConfirm && (
          <div className="custom-confirm-modal-overlay" style={{ zIndex: 10005 }}>
            <div className="custom-confirm-modal-card">
              <div className="confirm-modal-icon-wrap" style={{ background: 'rgba(239, 68, 68, 0.15)', borderColor: 'rgba(239, 68, 68, 0.4)', color: '#ef4444' }}>
                🍪
              </div>
              <h3 className="confirm-modal-title">{t.cookies.clearCookiesConfirmTitle}</h3>
              <p className="confirm-modal-desc">
                {t.cookies.clearCookiesConfirmDesc}
              </p>
              <div className="confirm-modal-actions">
                <button
                  type="button"
                  className="btn-confirm-cancel"
                  onClick={() => setShowDeleteConfirm(false)}
                  disabled={isLoading}
                >
                  {t.cookies.clearCookiesConfirmKeep}
                </button>
                <button
                  type="button"
                  className="btn-confirm-purge"
                  onClick={executeDeleteCookies}
                  disabled={isLoading}
                >
                  {isLoading ? t.cookies.deletingBtn : t.cookies.clearCookiesConfirmPurge}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
