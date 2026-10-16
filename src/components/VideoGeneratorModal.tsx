import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useLanguage } from '../locales';
import { resilientFetch } from '../utils/api';

export interface VideoGeneratorModalProps {
  onClose: () => void;
  apiKey: string;
  model: string;
  initialUrl?: string;
  initialPrompt?: string;
}

type Tab = 'toPrompt' | 'toVideo';

interface ShotItem {
  time: string;
  description: string;
}

interface PromptResult {
  video_id: string;
  title?: string;
  channel?: string;
  duration?: number;
  model?: string;
  prompt: string;
  summary?: string;
  style?: string;
  subject?: string;
  action?: string;
  setting?: string;
  camera?: string;
  lighting?: string;
  audio?: string;
  negative_prompt?: string;
  shot_list: ShotItem[];
  transcript?: string;
}

interface GeneratedFile {
  filename: string;
  download_url: string;
}

interface BatchItem {
  url: string;
  status: 'pending' | 'processing' | 'completed' | 'error';
  title?: string;
  prompt?: string;
  error?: string;
}

interface BatchStatus {
  batch_id: string;
  status: 'running' | 'completed' | 'error';
  total: number;
  completed: number;
  failed: number;
  skipped?: string[];
  items: BatchItem[];
}

// Style presets fill the free-text style field. Values stay in English because
// the backend forwards them verbatim into an English technical prompt; the
// labels are localised separately.
const STYLE_PRESETS = [
  'cinematic realism',
  'documentary',
  'anime',
  '3D animation',
  'vintage film',
  'hyperreal macro',
] as const;

interface GenerationStatus {
  job_id: string;
  status: string;
  stage?: string;
  progress?: number;
  elapsed?: number;
  model?: string;
  videos: GeneratedFile[];
  error?: string;
}

const POLL_INTERVAL_MS = 4000;

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback;
}

export const VideoGeneratorModal: React.FC<VideoGeneratorModalProps> = ({
  onClose,
  apiKey,
  model,
  initialUrl = '',
  initialPrompt = '',
}) => {
  const { t } = useLanguage();
  const v = t.videoGen;

  const [tab, setTab] = useState<Tab>(initialPrompt ? 'toVideo' : 'toPrompt');

  // Video -> prompt state
  const [url, setUrl] = useState(initialUrl);
  const [styleHint, setStyleHint] = useState('');
  const [extraInstructions, setExtraInstructions] = useState('');
  const [isExtracting, setIsExtracting] = useState(false);
  const [extractError, setExtractError] = useState<string | null>(null);
  const [promptResult, setPromptResult] = useState<PromptResult | null>(null);
  const [copied, setCopied] = useState(false);

  // Batch mode: a newline-separated list of sources instead of one URL.
  const [isBatchMode, setIsBatchMode] = useState(false);
  const [batchUrls, setBatchUrls] = useState('');
  const [batchStatus, setBatchStatus] = useState<BatchStatus | null>(null);

  // Prompt -> video state
  const [prompt, setPrompt] = useState(initialPrompt);
  const [negativePrompt, setNegativePrompt] = useState('');
  const [aspectRatio, setAspectRatio] = useState('16:9');
  const [resolution, setResolution] = useState('720p');
  const [duration, setDuration] = useState(8);
  const [numberOfVideos, setNumberOfVideos] = useState(1);
  const [videoModels, setVideoModels] = useState<string[]>([]);
  const [videoModel, setVideoModel] = useState('');
  const [isGenerating, setIsGenerating] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);
  const [genStatus, setGenStatus] = useState<GenerationStatus | null>(null);

  const pollRef = useRef<number | null>(null);

  // Load the available Veo models on mount. The parent mounts this component
  // only while the modal is open, so there is no `isOpen` gate to check here.
  useEffect(() => {
    if (videoModels.length) return;
    let cancelled = false;
    (async () => {
      try {
        const resp = await resilientFetch('/api/video-models', { silent: true });
        if (!resp.ok) return;
        const data = await resp.json();
        if (!cancelled && Array.isArray(data.models) && data.models.length) {
          setVideoModels(data.models);
          setVideoModel((prev) => prev || data.models[0]);
        }
      } catch {
        /* keep the fallback empty state; the backend validates the model anyway */
      }
    })();
    return () => { cancelled = true; };
  }, [videoModels.length]);

  const stopPolling = useCallback(() => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const handleExtractPrompt = async () => {
    if (!url.trim()) return;
    setIsExtracting(true);
    setExtractError(null);
    setPromptResult(null);
    try {
      const resp = await resilientFetch('/api/generate-prompt', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          url: url.trim(),
          api_key: apiKey,
          model,
          style_hint: styleHint.trim() || null,
          extra_instructions: extraInstructions.trim() || null,
        }),
        silent: true,
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error(data.detail || v.extractFailed);
      }
      setPromptResult(data as PromptResult);
    } catch (err) {
      setExtractError(errorMessage(err, v.extractFailed));
    } finally {
      setIsExtracting(false);
    }
  };

  const handleUsePromptForVideo = () => {
    if (!promptResult) return;
    setPrompt(promptResult.prompt);
    setNegativePrompt(promptResult.negative_prompt || '');
    setTab('toVideo');
  };

  const batchPollRef = useRef<number | null>(null);

  const stopBatchPolling = useCallback(() => {
    if (batchPollRef.current !== null) {
      window.clearInterval(batchPollRef.current);
      batchPollRef.current = null;
    }
  }, []);

  useEffect(() => stopBatchPolling, [stopBatchPolling]);

  const handleBatchExtract = async () => {
    const urls = batchUrls
      .split('\n')
      .map((line) => line.trim())
      .filter(Boolean);
    if (!urls.length) return;

    stopBatchPolling();
    setIsExtracting(true);
    setExtractError(null);
    setPromptResult(null);
    setBatchStatus(null);

    try {
      const resp = await resilientFetch('/api/generate-prompts-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          urls,
          api_key: apiKey,
          model,
          style_hint: styleHint.trim() || null,
          extra_instructions: extraInstructions.trim() || null,
        }),
        silent: true,
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error(data.detail || v.batchFailed);
      }
      const batchId = data.batch_id as string;

      // Poll until every item settles. The backend does the work in the
      // background, so this only reads progress; it never re-submits.
      batchPollRef.current = window.setInterval(async () => {
        try {
          const statusResp = await resilientFetch(
            `/api/generate-prompts-batch-status/${batchId}`,
            { silent: true },
          );
          if (!statusResp.ok) return;
          const status = (await statusResp.json()) as BatchStatus;
          setBatchStatus(status);
          if (status.status !== 'running') {
            stopBatchPolling();
            setIsExtracting(false);
          }
        } catch {
          /* transient poll failure: keep the interval and try again */
        }
      }, 2500);
    } catch (err) {
      setExtractError(errorMessage(err, v.batchFailed));
      setIsExtracting(false);
    }
  };

  const handleCopyBatchPrompt = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setExtractError(v.copyFailed);
    }
  };

  const handleCopyPrompt = async () => {
    if (!promptResult) return;
    try {
      await navigator.clipboard.writeText(promptResult.prompt);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setExtractError(v.copyFailed);
    }
  };

  const pollStatus = useCallback(async (jobId: string) => {
    try {
      const resp = await resilientFetch(`/api/generate-video-status/${jobId}`, { silent: true });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error(data.detail || v.generateFailed);
      }
      const status = data as GenerationStatus;
      setGenStatus(status);
      if (status.status === 'completed' || status.status === 'failed') {
        stopPolling();
        setIsGenerating(false);
        if (status.status === 'failed') {
          setGenError(status.error || v.generateFailed);
        }
      }
    } catch (err) {
      stopPolling();
      setIsGenerating(false);
      setGenError(errorMessage(err, v.generateFailed));
    }
  }, [stopPolling, v.generateFailed]);

  const handleGenerateVideo = async () => {
    if (!prompt.trim()) return;
    stopPolling();
    setIsGenerating(true);
    setGenError(null);
    setGenStatus(null);
    try {
      const resp = await resilientFetch('/api/generate-video', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt: prompt.trim(),
          api_key: apiKey,
          model: videoModel || null,
          aspect_ratio: aspectRatio,
          resolution,
          duration_seconds: duration,
          number_of_videos: numberOfVideos,
          negative_prompt: negativePrompt.trim() || null,
        }),
        silent: true,
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        throw new Error(data.detail || v.generateFailed);
      }
      setGenStatus({ job_id: data.job_id, status: data.status, videos: [] });
      pollRef.current = window.setInterval(() => pollStatus(data.job_id), POLL_INTERVAL_MS);
      // Check immediately so a fast failure surfaces without waiting a full interval.
      pollStatus(data.job_id);
    } catch (err) {
      setIsGenerating(false);
      setGenError(errorMessage(err, v.generateFailed));
    }
  };





  const fieldStyle: React.CSSProperties = {
    width: '100%',
    padding: '0.6rem 0.75rem',
    borderRadius: '8px',
    border: '1px solid rgba(var(--tint), 0.12)',
    background: 'rgba(var(--tint), 0.04)',
    color: 'var(--text-primary)',
    fontSize: '0.85rem',
    fontFamily: 'inherit',
  };
  const labelStyle: React.CSSProperties = {
    display: 'block',
    fontSize: '0.72rem',
    fontWeight: 700,
    textTransform: 'uppercase',
    letterSpacing: '0.04em',
    color: 'var(--text-secondary)',
    marginBottom: '0.35rem',
  };

  return (
    <div className="custom-confirm-modal-overlay" onClick={onClose}>
      <div
        className="custom-confirm-modal-card"
        style={{ maxWidth: '760px', width: '92vw', maxHeight: '90vh', overflowY: 'auto', textAlign: 'left' }}
        onClick={(e) => e.stopPropagation()}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
          <h3 className="confirm-modal-title" style={{ margin: 0 }}>🎬 {v.title}</h3>
          <button type="button" className="cookie-header-btn" onClick={onClose} style={{ padding: '0.3rem 0.7rem' }}>
            ✕
          </button>
        </div>

        <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1.25rem' }}>
          {(['toPrompt', 'toVideo'] as Tab[]).map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => setTab(key)}
              style={{
                flex: 1,
                padding: '0.55rem 0.75rem',
                borderRadius: '8px',
                fontSize: '0.82rem',
                fontWeight: 700,
                cursor: 'pointer',
                border: tab === key ? '1px solid var(--primary)' : '1px solid rgba(var(--tint), 0.1)',
                background: tab === key ? 'rgba(255,94,58,0.15)' : 'rgba(var(--tint), 0.03)',
                color: tab === key ? 'var(--primary)' : 'var(--text-secondary)',
              }}
            >
              {key === 'toPrompt' ? `📝 ${v.tabToPrompt}` : `✨ ${v.tabToVideo}`}
            </button>
          ))}
        </div>

        {tab === 'toPrompt' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.9rem' }}>
            <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', margin: 0 }}>
              {isBatchMode ? v.batchHint : v.toPromptHint}
            </p>

            <div style={{ display: 'flex', gap: '0.5rem' }}>
              {([false, true] as const).map((mode) => (
                <button
                  key={String(mode)}
                  type="button"
                  onClick={() => { setIsBatchMode(mode); setExtractError(null); }}
                  style={{
                    padding: '0.3rem 0.7rem',
                    borderRadius: '6px',
                    fontSize: '0.75rem',
                    fontWeight: 600,
                    cursor: 'pointer',
                    border: isBatchMode === mode ? '1px solid var(--primary)' : '1px solid rgba(var(--tint), 0.12)',
                    background: isBatchMode === mode ? 'rgba(255,94,58,0.15)' : 'rgba(var(--tint), 0.04)',
                    color: isBatchMode === mode ? 'var(--primary)' : 'var(--text-secondary)',
                  }}
                >
                  {mode ? `📚 ${v.batchToggle}` : `1️⃣ ${v.singleToggle}`}
                </button>
              ))}
            </div>

            <div>
              <label style={labelStyle}>{isBatchMode ? v.batchSourceLabel : v.sourceLabel}</label>
              {isBatchMode ? (
                <textarea
                  value={batchUrls}
                  onChange={(e) => setBatchUrls(e.target.value)}
                  rows={4}
                  placeholder={v.batchSourcePlaceholder}
                  style={{ ...fieldStyle, resize: 'vertical', lineHeight: 1.5 }}
                />
              ) : (
                <input
                  type="text"
                  style={fieldStyle}
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder={v.sourcePlaceholder}
                />
              )}
            </div>

            <div style={{ display: 'flex', gap: '0.75rem' }}>
              <div style={{ flex: 1 }}>
                <label style={labelStyle}>{v.styleLabel}</label>
                <input
                  type="text"
                  style={fieldStyle}
                  value={styleHint}
                  onChange={(e) => setStyleHint(e.target.value)}
                  placeholder={v.stylePlaceholder}
                />
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.35rem', marginTop: '0.4rem' }}>
                  {STYLE_PRESETS.map((preset) => {
                    const active = styleHint.trim().toLowerCase() === preset.toLowerCase();
                    return (
                      <button
                        key={preset}
                        type="button"
                        onClick={() => setStyleHint(active ? '' : preset)}
                        style={{
                          padding: '0.22rem 0.55rem',
                          borderRadius: '12px',
                          fontSize: '0.7rem',
                          fontWeight: 600,
                          cursor: 'pointer',
                          border: active ? '1px solid var(--primary)' : '1px solid rgba(var(--tint), 0.12)',
                          background: active ? 'rgba(255,94,58,0.15)' : 'rgba(var(--tint), 0.04)',
                          color: active ? 'var(--primary)' : 'var(--text-secondary)',
                        }}
                      >
                        {v.stylePresets[preset]}
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>

            <div>
              <label style={labelStyle}>{v.extraLabel}</label>
              <input
                type="text"
                style={fieldStyle}
                value={extraInstructions}
                onChange={(e) => setExtraInstructions(e.target.value)}
                placeholder={v.extraPlaceholder}
              />
            </div>

            <button
              type="button"
              className="glowing-btn"
              disabled={isExtracting || (isBatchMode ? !batchUrls.trim() : !url.trim())}
              onClick={isBatchMode ? handleBatchExtract : handleExtractPrompt}
              style={{ padding: '0.7rem 1rem', borderRadius: '8px', fontWeight: 700, opacity: isExtracting || (isBatchMode ? !batchUrls.trim() : !url.trim()) ? 0.6 : 1 }}
            >
              {isExtracting
                ? `⏳ ${v.extracting}`
                : `📝 ${isBatchMode ? v.batchExtractBtn : v.extractBtn}`}
            </button>

            {extractError && (
              <div style={{ padding: '0.7rem 0.85rem', borderRadius: '8px', background: 'rgba(244,63,94,0.12)', border: '1px solid rgba(244,63,94,0.35)', color: '#fca5a5', fontSize: '0.82rem' }}>
                {extractError}
              </div>
            )}

            {isBatchMode && batchStatus && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem', padding: '0.9rem', borderRadius: '10px', background: 'rgba(var(--tint), 0.03)', border: '1px solid rgba(var(--tint), 0.08)' }}>
                <div style={{ fontSize: '0.8rem', fontWeight: 700 }}>
                  {v.batchProgress
                    .replace('{done}', String(batchStatus.completed))
                    .replace('{failed}', String(batchStatus.failed))
                    .replace('{total}', String(batchStatus.total))}
                </div>
                {batchStatus.skipped && batchStatus.skipped.length > 0 && (
                  <div style={{ fontSize: '0.74rem', color: 'var(--text-secondary)' }}>
                    {v.batchSkippedDuplicates.replace('{count}', String(batchStatus.skipped.length))}
                  </div>
                )}
                {batchStatus.items.map((item, i) => (
                  <div key={`${item.url}-${i}`} style={{ display: 'flex', flexDirection: 'column', gap: '0.3rem', padding: '0.55rem 0.65rem', borderRadius: '8px', background: 'rgba(var(--tint), 0.03)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', fontSize: '0.76rem' }}>
                      <span>{item.status === 'completed' ? '✅' : item.status === 'error' ? '❌' : '⏳'}</span>
                      <span style={{ color: 'var(--text-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {item.title || item.url}
                      </span>
                    </div>
                    {item.error && (
                      <div style={{ fontSize: '0.74rem', color: '#fca5a5' }}>{item.error}</div>
                    )}
                    {item.prompt && (
                      <>
                        <textarea
                          readOnly
                          value={item.prompt}
                          rows={3}
                          style={{ ...fieldStyle, resize: 'vertical', lineHeight: 1.5, fontSize: '0.78rem' }}
                        />
                        <div style={{ display: 'flex', gap: '0.4rem' }}>
                          <button
                            type="button"
                            className="cookie-header-btn"
                            onClick={() => handleCopyBatchPrompt(item.prompt as string)}
                            style={{ padding: '0.3rem 0.6rem', fontSize: '0.72rem' }}
                          >
                            📋 {v.copyBtn}
                          </button>
                          <button
                            type="button"
                            className="cookie-header-btn"
                            onClick={() => { setPrompt(item.prompt as string); setTab('toVideo'); }}
                            style={{ padding: '0.3rem 0.6rem', fontSize: '0.72rem' }}
                          >
                            ✨ {v.usePromptBtn}
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                ))}
              </div>
            )}

            {promptResult && (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', padding: '0.9rem', borderRadius: '10px', background: 'rgba(var(--tint), 0.03)', border: '1px solid rgba(var(--tint), 0.08)' }}>
                {promptResult.title && (
                  <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                    <strong>{promptResult.title}</strong>
                    {promptResult.model ? ` · ${promptResult.model}` : ''}
                  </div>
                )}

                <div>
                  <label style={labelStyle}>{v.promptLabel}</label>
                  <textarea
                    readOnly
                    value={promptResult.prompt}
                    rows={6}
                    style={{ ...fieldStyle, resize: 'vertical', lineHeight: 1.5 }}
                  />
                </div>

                {promptResult.summary && (
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                    <strong>{v.summaryLabel}:</strong> {promptResult.summary}
                  </div>
                )}

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem', fontSize: '0.78rem' }}>
                  {([
                    [v.styleLabel, promptResult.style],
                    [v.cameraLabel, promptResult.camera],
                    [v.lightingLabel, promptResult.lighting],
                    [v.audioLabel, promptResult.audio],
                    [v.settingLabel, promptResult.setting],
                    [v.negativeLabel, promptResult.negative_prompt],
                  ] as [string, string | undefined][]).filter(([, val]) => val).map(([label, val]) => (
                    <div key={label} style={{ padding: '0.5rem 0.6rem', borderRadius: '7px', background: 'rgba(var(--tint), 0.03)' }}>
                      <div style={{ fontSize: '0.68rem', textTransform: 'uppercase', color: 'var(--text-secondary)', fontWeight: 700 }}>{label}</div>
                      <div style={{ color: 'var(--text-primary)' }}>{val}</div>
                    </div>
                  ))}
                </div>

                {promptResult.shot_list?.length > 0 && (
                  <div>
                    <label style={labelStyle}>{v.shotListLabel}</label>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.35rem' }}>
                      {promptResult.shot_list.map((shot, i) => (
                        <div key={i} style={{ display: 'flex', gap: '0.6rem', fontSize: '0.78rem' }}>
                          <span style={{ minWidth: '62px', color: 'var(--primary)', fontWeight: 700 }}>{shot.time}</span>
                          <span style={{ color: 'var(--text-secondary)' }}>{shot.description}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap' }}>
                  <button type="button" className="cookie-header-btn" onClick={handleCopyPrompt} style={{ padding: '0.5rem 0.85rem' }}>
                    {copied ? `✅ ${v.copied}` : `📋 ${v.copyBtn}`}
                  </button>
                  <button
                    type="button"
                    className="glowing-btn"
                    onClick={handleUsePromptForVideo}
                    style={{ padding: '0.5rem 0.95rem', borderRadius: '8px', fontWeight: 700 }}
                  >
                    ✨ {v.usePromptBtn}
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        {tab === 'toVideo' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.9rem' }}>
            <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', margin: 0 }}>{v.toVideoHint}</p>

            <div>
              <label style={labelStyle}>{v.promptLabel}</label>
              <textarea
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                rows={6}
                placeholder={v.promptPlaceholder}
                style={{ ...fieldStyle, resize: 'vertical', lineHeight: 1.5 }}
              />
            </div>

            <div>
              <label style={labelStyle}>{v.negativeLabel}</label>
              <input
                type="text"
                style={fieldStyle}
                value={negativePrompt}
                onChange={(e) => setNegativePrompt(e.target.value)}
                placeholder={v.negativePlaceholder}
              />
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '0.75rem' }}>
              <div>
                <label style={labelStyle}>{v.modelLabel}</label>
                <select style={fieldStyle} value={videoModel} onChange={(e) => setVideoModel(e.target.value)}>
                  {(videoModels.length ? videoModels : ['veo-3.1-generate-preview']).map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              </div>
              <div>
                <label style={labelStyle}>{v.aspectLabel}</label>
                <select style={fieldStyle} value={aspectRatio} onChange={(e) => setAspectRatio(e.target.value)}>
                  <option value="16:9">16:9 — Landscape</option>
                  <option value="9:16">9:16 — Portrait</option>
                </select>
              </div>
              <div>
                <label style={labelStyle}>{v.resolutionLabel}</label>
                <select style={fieldStyle} value={resolution} onChange={(e) => setResolution(e.target.value)}>
                  <option value="720p">720p</option>
                  <option value="1080p">1080p</option>
                  <option value="4k">4k</option>
                </select>
              </div>
              <div>
                <label style={labelStyle}>{v.durationLabel}</label>
                <select style={fieldStyle} value={duration} onChange={(e) => setDuration(Number(e.target.value))}>
                  <option value={4}>4s</option>
                  <option value={6}>6s</option>
                  <option value={8}>8s</option>
                </select>
              </div>
              <div>
                <label style={labelStyle}>{v.countLabel}</label>
                <select style={fieldStyle} value={numberOfVideos} onChange={(e) => setNumberOfVideos(Number(e.target.value))}>
                  {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n}</option>)}
                </select>
              </div>
            </div>

            <button
              type="button"
              className="glowing-btn"
              disabled={isGenerating || !prompt.trim()}
              onClick={handleGenerateVideo}
              style={{ padding: '0.7rem 1rem', borderRadius: '8px', fontWeight: 700, opacity: isGenerating || !prompt.trim() ? 0.6 : 1 }}
            >
              {isGenerating ? `⏳ ${v.generating}` : `✨ ${v.generateBtn}`}
            </button>

            <div style={{ fontSize: '0.72rem', color: 'var(--text-secondary)' }}>{v.veoNotice}</div>

            {genError && (
              <div style={{ padding: '0.7rem 0.85rem', borderRadius: '8px', background: 'rgba(244,63,94,0.12)', border: '1px solid rgba(244,63,94,0.35)', color: '#fca5a5', fontSize: '0.82rem' }}>
                {genError}
              </div>
            )}

            {genStatus && (
              <div style={{ padding: '0.9rem', borderRadius: '10px', background: 'rgba(var(--tint), 0.03)', border: '1px solid rgba(var(--tint), 0.08)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: genStatus.videos.length ? '0.75rem' : 0 }}>
                  {isGenerating && <span className="spinner-icon">⏳</span>}
                  <span style={{ fontSize: '0.85rem', fontWeight: 600 }}>
                    {genStatus.stage || genStatus.status}
                    {genStatus.elapsed ? ` · ${genStatus.elapsed}s` : ''}
                  </span>
                </div>

                {isGenerating && (
                  <div style={{ height: '4px', borderRadius: '2px', background: 'rgba(var(--tint), 0.1)', overflow: 'hidden', marginTop: '0.5rem' }}>
                    <div style={{ height: '100%', width: '35%', background: 'var(--primary)', animation: 'pulse 1.6s ease-in-out infinite' }} />
                  </div>
                )}

                {genStatus.videos.length > 0 && (
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '0.75rem', marginTop: '0.5rem' }}>
                    {genStatus.videos.map((file) => (
                      <div key={file.filename} style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                        <video
                          src={file.download_url}
                          controls
                          style={{ width: '100%', borderRadius: '8px', background: '#000' }}
                        />
                        <a
                          href={file.download_url}
                          download={file.filename}
                          className="cookie-header-btn"
                          style={{ padding: '0.4rem 0.7rem', textAlign: 'center', textDecoration: 'none', fontSize: '0.78rem' }}
                        >
                          ⬇️ {v.downloadBtn}
                        </a>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
