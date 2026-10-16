import type {
  AspectRatioOption,
  BackgroundStyle,
  CaptionFont,
  CaptionStyle,
  FacecamPosition,
  FontSizeOption,
  HardwareAccelOption,
  StreamerPreset,
  SubtitlePositionMode,
  TextCaseOption,
  TitleDurationOption,
  TitlePosition,
} from '../types';

export const STUDIO_PREFS_KEY = 'cheat_clip_studio_preferences';

export interface StudioPreferences {
  aspectRatio?: AspectRatioOption;
  backgroundStyle?: BackgroundStyle;
  enableFaceTracking?: boolean;
  streamerPreset?: StreamerPreset;
  facecamPosition?: FacecamPosition;
  titlePosition?: TitlePosition;
  captionStyle?: CaptionStyle;
  captionFont?: CaptionFont;
  titleFont?: CaptionFont;
  fontSize?: FontSizeOption;
  fontSizePx?: number;
  titleFontSize?: FontSizeOption;
  titleFontSizePx?: number;
  textCase?: TextCaseOption;
  titleTextCase?: TextCaseOption;
  subtitlePositionMode?: SubtitlePositionMode;
  titleDuration?: TitleDurationOption;
  originalAudioVolume?: number;
  hardwareAccel?: HardwareAccelOption;
  titleYPercent?: number;
  subtitleYPercent?: number;
  subtitleCenterYPercent?: number;
  isCustomTitleY?: boolean;
  bgmVolume?: number;
  hookSfxVolume?: number;
  renderMode?: 'separate' | 'merged';
  fileNamePrefix?: string;
  fileNameSuffix?: string;
  titlePrefix?: string;
  titleSuffix?: string;
}

export const DEFAULT_STUDIO_PREFS: Required<StudioPreferences> = {
  aspectRatio: '9:16',
  backgroundStyle: 'black',
  enableFaceTracking: true,
  streamerPreset: 'none',
  facecamPosition: 'auto',
  titlePosition: 'auto',
  captionStyle: 'viral_pop',
  captionFont: 'Outfit',
  titleFont: 'Outfit',
  fontSize: 'medium',
  fontSizePx: 75,
  titleFontSize: 'medium',
  titleFontSizePx: 75,
  textCase: 'uppercase',
  titleTextCase: 'uppercase',
  subtitlePositionMode: 'bottom',
  titleDuration: 'entire',
  originalAudioVolume: 100,
  hardwareAccel: 'auto',
  titleYPercent: 17,
  subtitleYPercent: 21,
  subtitleCenterYPercent: 50,
  isCustomTitleY: false,
  bgmVolume: 25,
  hookSfxVolume: 100,
  renderMode: 'separate',
  fileNamePrefix: '',
  fileNameSuffix: '',
  titlePrefix: '',
  titleSuffix: '',
};

export function getSavedStudioPreferences(): Partial<StudioPreferences> {
  try {
    const raw = localStorage.getItem(STUDIO_PREFS_KEY);
    if (!raw) return {};
    return JSON.parse(raw) as Partial<StudioPreferences>;
  } catch (e) {
    console.warn('Failed to parse studio preferences from localStorage:', e);
    return {};
  }
}
