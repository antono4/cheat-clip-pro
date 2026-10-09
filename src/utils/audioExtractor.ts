/**
 * Client-side audio extractor & video metadata reader using Web Audio API & HTML5 Video.
 * Extracts lightweight WAV audio directly in the user's browser, reducing upload payload by >95%
 * (e.g. a 1GB video upload becomes a ~10-25MB audio file sent to server).
 */

export interface VideoClientMetadata {
  duration: number;
  width: number;
  height: number;
  objectUrl: string;
}

/**
 * Reads video duration, width, and height client-side via HTML5 video element.
 */
export async function extractVideoClientMetadata(file: File): Promise<VideoClientMetadata> {
  return new Promise((resolve, reject) => {
    const video = document.createElement('video');
    video.preload = 'metadata';
    const objectUrl = URL.createObjectURL(file);

    video.onloadedmetadata = () => {
      const duration = Number(video.duration) || 0;
      const width = video.videoWidth || 1920;
      const height = video.videoHeight || 1080;
      resolve({ duration, width, height, objectUrl });
    };

    video.onerror = () => {
      URL.revokeObjectURL(objectUrl);
      reject(new Error('Unable to read video file metadata in browser. Please check format.'));
    };

    video.src = objectUrl;
  });
}

/**
 * Converts an AudioBuffer to a standard mono 16kHz WAV Blob (OpenAI Whisper compatible format).
 */
export function audioBufferToWav(buffer: AudioBuffer): Blob {
  const numChannels = 1; // mono is optimal for AI speech recognition
  const sampleRate = buffer.sampleRate;
  const bitDepth = 16;
  const bytesPerSample = bitDepth / 8;
  const blockAlign = numChannels * bytesPerSample;

  // Downmix to mono if multi-channel
  const channelData = new Float32Array(buffer.length);
  const numSrcChannels = buffer.numberOfChannels;
  for (let c = 0; c < numSrcChannels; c++) {
    const src = buffer.getChannelData(c);
    for (let i = 0; i < buffer.length; i++) {
      channelData[i] += src[i] / numSrcChannels;
    }
  }

  const dataSize = channelData.length * bytesPerSample;
  const headerSize = 44;
  const totalSize = headerSize + dataSize;
  const arrayBuffer = new ArrayBuffer(totalSize);
  const view = new DataView(arrayBuffer);

  function writeString(offset: number, str: string) {
    for (let i = 0; i < str.length; i++) {
      view.setUint8(offset + i, str.charCodeAt(i));
    }
  }

  // RIFF Chunk
  writeString(0, 'RIFF');
  view.setUint32(4, totalSize - 8, true);
  writeString(8, 'WAVE');

  // fmt sub-chunk
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true); // PCM SubChunk1Size
  view.setUint16(20, 1, true); // PCM format
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * blockAlign, true); // ByteRate
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, bitDepth, true);

  // data sub-chunk
  writeString(36, 'data');
  view.setUint32(40, dataSize, true);

  // Write 16-bit PCM samples
  let offset = 44;
  for (let i = 0; i < channelData.length; i++, offset += 2) {
    const s = Math.max(-1, Math.min(1, channelData[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }

  return new Blob([arrayBuffer], { type: 'audio/wav' });
}

/**
 * Extracts audio from a video File in the browser using Web Audio API.
 * Returns a lightweight WAV File ready for speech transcription.
 */
export async function extractAudioFromVideoClient(
  videoFile: File,
  onProgress?: (stage: string) => void
): Promise<{ audioFile: File; metadata: VideoClientMetadata }> {
  onProgress?.('Inspecting video stream and metadata...');
  const metadata = await extractVideoClientMetadata(videoFile);

  try {
    onProgress?.('Decoding audio track in browser memory (Web Audio API)...');
    const arrayBuf = await videoFile.arrayBuffer();

    const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;
    const audioCtx = new AudioContextClass({ sampleRate: 16000 }); // Downsample to 16kHz for Whisper speed

    let audioBuffer: AudioBuffer;
    try {
      audioBuffer = await audioCtx.decodeAudioData(arrayBuf);
    } finally {
      await audioCtx.close();
    }

    onProgress?.('Encoding compressed 16kHz mono audio for AI...');
    const wavBlob = audioBufferToWav(audioBuffer);
    const cleanBaseName = videoFile.name.replace(/\.[^/.]+$/, '');
    const audioFile = new File([wavBlob], `${cleanBaseName}_audio.wav`, { type: 'audio/wav' });

    return { audioFile, metadata };
  } catch (err) {
    console.warn('Client-side Web Audio decode failed or unsupported codec, falling back to direct stream:', err);
    // Return original video file as graceful fallback
    return { audioFile: videoFile, metadata };
  }
}
