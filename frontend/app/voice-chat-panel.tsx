'use client';

import { useEffect, useRef, useState } from 'react';
import { Mic, Play, Send, Square, Trash2 } from 'lucide-react';

export type VoiceClip = { url: string; duration: number };

type SpeechResult = { transcript: string };
type SpeechResultEvent = { results: ArrayLike<{ isFinal: boolean; [index: number]: SpeechResult }> };
type SpeechRecognizer = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: SpeechResultEvent) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
};
type SpeechWindow = Window & {
  SpeechRecognition?: new () => SpeechRecognizer;
  webkitSpeechRecognition?: new () => SpeechRecognizer;
};
type Props = {
  open: boolean;
  onClose: () => void;
  onSend: (transcript: string, clip: VoiceClip) => void;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
  disabled: boolean;
};

const formatTime = (seconds: number) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;

async function transcriptionWav(recording: Blob): Promise<Blob> {
  const decoder = new AudioContext();
  try {
    const decoded = await decoder.decodeAudioData(await recording.arrayBuffer());
    if (!decoded.duration || decoded.duration > 90.5) throw new Error('Record a note of up to 90 seconds.');
    const renderer = new OfflineAudioContext(1, Math.ceil(decoded.duration * 16000), 16000);
    const source = renderer.createBufferSource();
    source.buffer = decoded;
    source.connect(renderer.destination);
    source.start();
    const mono = await renderer.startRendering();
    const samples = mono.getChannelData(0);
    const bytes = new ArrayBuffer(44 + samples.length * 2);
    const wav = new DataView(bytes);
    const label = (offset: number, value: string) => {
      for (let index = 0; index < value.length; index++) wav.setUint8(offset + index, value.charCodeAt(index));
    };
    label(0, 'RIFF');
    wav.setUint32(4, bytes.byteLength - 8, true);
    label(8, 'WAVE');
    label(12, 'fmt ');
    wav.setUint32(16, 16, true);
    wav.setUint16(20, 1, true);
    wav.setUint16(22, 1, true);
    wav.setUint32(24, 16000, true);
    wav.setUint32(28, 32000, true);
    wav.setUint16(32, 2, true);
    wav.setUint16(34, 16, true);
    label(36, 'data');
    wav.setUint32(40, samples.length * 2, true);
    for (let index = 0; index < samples.length; index++) {
      const sample = Math.max(-1, Math.min(1, samples[index]));
      wav.setInt16(44 + index * 2, sample * (sample < 0 ? 32768 : 32767), true);
    }
    return new Blob([bytes], { type: 'audio/wav' });
  } finally {
    await decoder.close();
  }
}

export default function VoiceChatPanel({ open, onClose, onSend, disabled, apiFetch }: Props) {
  const [recording, setRecording] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [transcript, setTranscript] = useState('');
  const [previewUrl, setPreviewUrl] = useState('');
  const [error, setError] = useState('');
  const [playing, setPlaying] = useState(false);
  const [sendAfterStop, setSendAfterStop] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const sendingRef = useRef(false);
  const transcriptionAbort = useRef<AbortController | null>(null);
  const transcriptRef = useRef('');
  const recordingRef = useRef(false);
  const recognitionFailed = useRef(false);
  const recognitionSettled = useRef<Promise<void> | null>(null);
  const settleRecognition = useRef<() => void>(() => {});
  const recorderRef = useRef<MediaRecorder | null>(null);
  const recognizerRef = useRef<SpeechRecognizer | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const previewUrlRef = useRef('');
  const transferredRef = useRef('');
  const startedRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const closedRef = useRef(false);
  const startCaptureRef = useRef<() => Promise<void>>(async () => {});
  const swipeStartX = useRef<number | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  function updateTranscript(value: string) {
    transcriptRef.current = value;
    setTranscript(value);
  }

  function releaseStream() {
    streamRef.current?.getTracks().forEach(track => track.stop());
    streamRef.current = null;
  }

  function stopCapture() {
    recordingRef.current = false;
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
    try { recognizerRef.current?.stop(); } catch { /* Recognition may already have stopped. */ }
    if (recorderRef.current?.state === 'recording') {
      setProcessing(true);
      recorderRef.current.stop();
    } else {
      releaseStream();
      setProcessing(false);
    }
    setRecording(false);
  }

  function discard() {
    recordingRef.current = false;
    transcriptionAbort.current?.abort();
    sendingRef.current = false;
    setTranscribing(false);
    settleRecognition.current();
    setSendAfterStop(false);
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
    try { recognizerRef.current?.abort(); } catch { /* Already stopped. */ }
    recognizerRef.current = null;
    if (recorderRef.current?.state === 'recording') {
      recorderRef.current.onstop = null;
      recorderRef.current.stop();
    }
    recorderRef.current = null;
    releaseStream();
    if (previewUrlRef.current && previewUrlRef.current !== transferredRef.current) URL.revokeObjectURL(previewUrlRef.current);
    previewUrlRef.current = '';
    setPreviewUrl('');
    updateTranscript('');
    setElapsed(0);
    setError('');
    setRecording(false);
    setProcessing(false);
    setPlaying(false);
  }

  useEffect(() => {
    if (!open) return;
    closedRef.current = false;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCloseRef.current();
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      closedRef.current = true;
      recordingRef.current = false;
      transcriptionAbort.current?.abort();
      settleRecognition.current();
      window.removeEventListener('keydown', onKeyDown);
      if (timerRef.current) clearInterval(timerRef.current);
      try { recognizerRef.current?.abort(); } catch { /* Ignore. */ }
      recognizerRef.current = null;
      if (recorderRef.current?.state === 'recording') {
        recorderRef.current.onstop = null;
        recorderRef.current.stop();
      }
      recorderRef.current = null;
      streamRef.current?.getTracks().forEach(track => track.stop());
      streamRef.current = null;
      if (previewUrlRef.current && previewUrlRef.current !== transferredRef.current) URL.revokeObjectURL(previewUrlRef.current);
      previewUrlRef.current = '';
      previous?.focus();
    };
  }, [open]);

  async function startCapture() {
    if (recording || processing || disabled) return;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setError('Voice notes require microphone recording support. Try Chrome or Edge over HTTPS.');
      return;
    }
    discard();
    setError('');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (closedRef.current) { stream.getTracks().forEach(track => track.stop()); return; }
      streamRef.current = stream;
      const recorder = new MediaRecorder(stream);
      const chunks: BlobPart[] = [];
      recorderRef.current = recorder;
      recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
      recorder.onerror = () => { setError('Recording failed. Please try again.'); stopCapture(); };
      recorder.onstop = () => {
        releaseStream();
        recorderRef.current = null;
        setProcessing(false);
        if (closedRef.current) return;
        if (!chunks.length) { setSendAfterStop(false); setError('No audio was captured. Please try again.'); return; }
        const url = URL.createObjectURL(new Blob(chunks, { type: recorder.mimeType || 'audio/webm' }));
        previewUrlRef.current = url;
        setPreviewUrl(url);
      };
      recorder.start(250);
      startedRef.current = Date.now();
      setRecording(true);
      recordingRef.current = true;
      recognitionFailed.current = false;
      timerRef.current = setInterval(() => {
        const seconds = Math.min(90, Math.floor((Date.now() - startedRef.current) / 1000));
        setElapsed(seconds);
        if (seconds >= 90) stopCapture();
      }, 250);

      // Speech recognition converts the voice note to text for the existing
      // Naija Bites agent. Audio itself is not uploaded to the backend.
      const speechWindow = window as SpeechWindow;
      const Recognizer = speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition;
      if (!Recognizer) {
        recognitionFailed.current = true;
        return;
      }
      const recognizer = new Recognizer();
      recognizerRef.current = recognizer;
      recognitionSettled.current = new Promise(resolve => { settleRecognition.current = resolve; });
      let previousSpeech = '';
      recognizer.lang = 'en-NG';
      recognizer.continuous = true;
      recognizer.interimResults = true;
      recognizer.onresult = event => {
        let text = '';
        for (let i = 0; i < event.results.length; i++) text += event.results[i][0]?.transcript ?? '';
        updateTranscript((previousSpeech + ' ' + text).trim());
      };
      recognizer.onerror = event => {
        if (event.error !== 'aborted' && event.error !== 'no-speech') recognitionFailed.current = true;
      };
      recognizer.onend = () => {
        previousSpeech = transcriptRef.current;
        if (recordingRef.current && !closedRef.current && !recognitionFailed.current) {
          try { recognizer.start(); return; } catch { recognitionFailed.current = true; }
        }
        if (recognizerRef.current === recognizer) recognizerRef.current = null;
        settleRecognition.current();
      };
      try { recognizer.start(); }
      catch { recognitionFailed.current = true; recognizerRef.current = null; settleRecognition.current(); }
    } catch {
      releaseStream();
      setRecording(false);
      setError('Microphone access failed. Allow access in your browser and try again.');
    }
  }

  startCaptureRef.current = startCapture;
  // Tapping the microphone starts recording immediately in the chat composer.
  useEffect(() => {
    if (open) void startCaptureRef.current();
  }, [open]);

  async function sendNote() {
    if (recording || processing || disabled || !previewUrl || sendingRef.current) return;
    sendingRef.current = true;
    setTranscribing(true);
    setError('');
    const controller = new AbortController();
    transcriptionAbort.current = controller;
    const timeout = window.setTimeout(() => controller.abort(), 45000);
    try {
      // Browser recognition may produce its final words after MediaRecorder stops.
      if (recognitionSettled.current) {
        await Promise.race([
          recognitionSettled.current,
          new Promise<void>(resolve => window.setTimeout(resolve, 1500)),
        ]);
      }
      if (closedRef.current || controller.signal.aborted) return;
      let text = transcriptRef.current.trim();
      if (!text || recognitionFailed.current || recognizerRef.current) {
        const recorded = await fetch(previewUrl, { signal: controller.signal }).then(response => response.blob());
        const audio = await transcriptionWav(recorded);
        if (closedRef.current || controller.signal.aborted) return;
        const response = await apiFetch('/voice/transcribe', {
          method: 'POST', body: audio,
          headers: { 'Content-Type': audio.type || 'audio/webm' },
          signal: controller.signal,
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || 'Could not transcribe your recording. Please try sending again.');
        text = typeof result.transcript === 'string' ? result.transcript.trim() : '';
        if (!text) throw new Error("I couldn't hear any words. Please record your note again.");
        updateTranscript(text);
      }
      if (closedRef.current || controller.signal.aborted) return;
      transferredRef.current = previewUrl;
      onSend(text, { url: previewUrl, duration: Math.max(1, elapsed) });
      onClose();
    } catch (cause) {
      if (!closedRef.current) {
        setError(cause instanceof Error && cause.name !== 'AbortError'
          ? cause.message : 'Sending timed out. Your recording is kept; try Send again.');
      }
    } finally {
      window.clearTimeout(timeout);
      transcriptionAbort.current = null;
      sendingRef.current = false;
      if (!closedRef.current) {
        setSendAfterStop(false);
        setTranscribing(false);
      }
    }
  }

  // Sending while recording stops capture first. The audio and final transcript
  // are committed only after MediaRecorder has produced the playable clip.
  useEffect(() => {
    if (!sendAfterStop || recording || processing || !previewUrl || disabled) return;
    setSendAfterStop(false);
    void sendNote();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sendAfterStop, recording, processing, previewUrl, transcript, disabled]);

  function handlePrimaryAction() {
    if (recording) {
      setSendAfterStop(true);
      stopCapture();
    } else if (previewUrl) {
      void sendNote();
    } else {
      void startCapture();
    }
  }

  if (!open) return null;
  return (
    <div className="voice-composer-wrap">
      <section className="composer voice-inline-panel" aria-label="Voice note composer" onTouchStart={event => { swipeStartX.current = event.touches[0]?.clientX ?? null; }} onTouchEnd={event => { const x = event.changedTouches[0]?.clientX; if (swipeStartX.current !== null && x !== undefined && swipeStartX.current - x > 75) onClose(); swipeStartX.current = null; }}>
        <button type="button" className="voice-inline-cancel" onClick={onClose} aria-label="Cancel and discard voice note" title="Discard voice note">
          <Trash2 size={19}/>
        </button>
        <span className={`voice-inline-mic ${recording ? 'is-recording' : ''}`} aria-label={recording ? 'Recording' : 'Voice note'}>
          <Mic size={21}/>
        </span>
        <div className="voice-inline-middle">
          <div className={`voice-inline-wave ${recording ? 'is-recording' : ''}`} aria-hidden="true">
            {Array.from({ length: 32 }, (_, i) => <i key={i} style={{ height: `${6 + (i * 17 + i * i * 7) % 25}px` }}/>)}
          </div>
          <span className="voice-inline-timer" aria-label={`Recording duration ${formatTime(elapsed)}`}>{formatTime(elapsed)}</span>
          <span className="voice-inline-swipe-hint" aria-hidden="true">‹ Slide to cancel</span>
        </div>
        {recording && (
          <button type="button" className="voice-inline-stop" onClick={stopCapture} aria-label="Stop recording to preview" title="Stop recording">
            <Square size={16} fill="currentColor"/>
          </button>
        )}
        {!recording && previewUrl && (
          <button type="button" className="voice-inline-play" onClick={() => {
            if (!audioRef.current) return;
            if (playing) { audioRef.current.pause(); setPlaying(false); }
            else void audioRef.current.play().then(() => setPlaying(true)).catch(() => setError('Could not play this recording.'));
          }} aria-label={playing ? 'Pause voice note' : 'Preview voice note'} title="Preview voice note">
            <Play size={18} fill="currentColor"/>
          </button>
        )}
        <button type="button" className="voice-inline-send" onClick={handlePrimaryAction}
          disabled={processing || disabled || sendAfterStop || transcribing}
          aria-label={recording ? 'Finish and send voice note' : previewUrl ? 'Send voice note' : 'Try recording again'}
          title={recording ? 'Finish and send' : previewUrl ? 'Send voice note' : 'Record'}>
          {processing || sendAfterStop || transcribing ? <span className="voice-inline-sending">···</span> : <Send size={21}/>}
        </button>
      </section>
      {previewUrl && <audio ref={audioRef} src={previewUrl} onEnded={() => setPlaying(false)} onPause={() => setPlaying(false)} preload="metadata"/>}
      {transcribing && <span className="voice-inline-help" role="status">Sending voice note…</span>}
      {error && <div className="voice-inline-help voice-inline-error" role="alert">{error}</div>}
      {previewUrl && transcript.trim() && (
        <details className="voice-inline-transcript">
          <summary>Edit transcript</summary>
          <label htmlFor="voice-inline-edit">Words sent to Naija Bites</label>
          <textarea id="voice-inline-edit" rows={2} value={transcript} onChange={event => updateTranscript(event.target.value)}/>
        </details>
      )}
      <span className="sr-only">Voice recordings may be sent for transcription. The transcript is sent to the assistant.</span>
    </div>
  );
}
