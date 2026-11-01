'use client';

import { useEffect, useRef, useState } from 'react';
import { Mic, Play, Send, Square, Trash2, X } from 'lucide-react';

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
  disabled: boolean;
};

const formatTime = (seconds: number) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;

export default function VoiceChatPanel({ open, onClose, onSend, disabled }: Props) {
  const [recording, setRecording] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [transcript, setTranscript] = useState('');
  const [previewUrl, setPreviewUrl] = useState('');
  const [error, setError] = useState('');
  const [playing, setPlaying] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const recognizerRef = useRef<SpeechRecognizer | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const previewUrlRef = useRef('');
  const transferredRef = useRef('');
  const startedRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const closedRef = useRef(false);
  const closeRef = useRef<HTMLButtonElement>(null);
  const startCaptureRef = useRef<() => Promise<void>>(async () => {});
  const touchStartY = useRef<number | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  function releaseStream() {
    streamRef.current?.getTracks().forEach(track => track.stop());
    streamRef.current = null;
  }

  function stopCapture() {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
    try { recognizerRef.current?.stop(); } catch { /* Recognition may already have stopped. */ }
    recognizerRef.current = null;
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
    setTranscript('');
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
        if (!chunks.length) { setError('No audio was captured. Please try again.'); return; }
        const url = URL.createObjectURL(new Blob(chunks, { type: recorder.mimeType || 'audio/webm' }));
        previewUrlRef.current = url;
        setPreviewUrl(url);
      };
      recorder.start(250);
      startedRef.current = Date.now();
      setRecording(true);
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
        setError('This browser cannot transcribe speech automatically. Type what you said before sending, or use Chrome/Edge.');
        return;
      }
      const recognizer = new Recognizer();
      recognizerRef.current = recognizer;
      recognizer.lang = 'en-NG';
      recognizer.continuous = true;
      recognizer.interimResults = true;
      recognizer.onresult = event => {
        let text = '';
        for (let i = 0; i < event.results.length; i++) text += event.results[i][0]?.transcript ?? '';
        setTranscript(text.trim());
      };
      recognizer.onerror = event => {
        if (event.error !== 'aborted') setError('Speech transcription was interrupted. Check or edit the words before sending.');
      };
      recognizer.onend = () => { if (recognizerRef.current === recognizer) recognizerRef.current = null; };
      try { recognizer.start(); }
      catch { setError('Automatic transcription is unavailable. You can type what you said below.'); }
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

  function sendNote() {
    if (recording || processing || disabled || !previewUrl || !transcript.trim()) return;
    transferredRef.current = previewUrl;
    onSend(transcript.trim(), { url: previewUrl, duration: Math.max(1, elapsed) });
    onClose();
  }

  if (!open) return null;
  return (
    <section className="voice-inline-panel" aria-label="Record voice note" onTouchStart={event => {
      if (event.target instanceof Element && event.target.closest('.voice-inline-grip')) touchStartY.current = event.touches[0]?.clientY ?? null;
    }} onTouchEnd={event => {
      const endY = event.changedTouches[0]?.clientY;
      if (touchStartY.current !== null && endY !== undefined && endY - touchStartY.current > 65) onClose();
      touchStartY.current = null;
    }}>
      <div className="voice-inline-grip" aria-hidden="true"><span/></div>
      <div className="voice-inline-top">
        <button ref={closeRef} type="button" className="voice-inline-cancel" onClick={onClose} aria-label="Cancel recording"><X size={19}/></button>
        <span className="voice-inline-heading">{recording ? 'Recording voice note' : processing ? 'Preparing voice note' : previewUrl ? 'Your voice note' : 'Voice note'}</span>
        <button type="button" className="voice-inline-discard" onClick={onClose} aria-label="Discard voice note"><Trash2 size={19}/></button>
      </div>
      <div className="voice-inline-mic"><Mic size={23}/></div>
      <div className="voice-inline-timer" aria-live="off">{formatTime(elapsed)}</div>
      <div className={`voice-inline-wave ${recording ? 'is-recording' : ''}`} aria-hidden="true">
        {Array.from({ length: 35 }, (_, i) => <i key={i} style={{ height: `${7 + (i * 17 + i * i * 7) % 29}px` }}/>)}
      </div>
      <p className="voice-inline-status" role="status">{recording ? 'Speak naturally, then tap stop' : processing ? 'Preparing your recording…' : previewUrl ? 'Ready to send' : error ? 'Unable to start recording' : 'Starting microphone…'}</p>
      <div className="voice-inline-actions">
        {recording ? (
          <button type="button" className="voice-inline-stop" onClick={stopCapture}><Square size={17} fill="currentColor"/> Stop</button>
        ) : previewUrl ? (
          <>
            <button type="button" className="voice-inline-play" onClick={() => {
              if (!audioRef.current) return;
              if (playing) { audioRef.current.pause(); setPlaying(false); }
              else void audioRef.current.play().then(() => setPlaying(true)).catch(() => setError('Could not play this recording.'));
            }} aria-label={playing ? 'Pause recording' : 'Play recording'}><Play size={18} fill="currentColor"/> {playing ? 'Playing' : 'Listen'}</button>
            <button type="button" className="voice-inline-send" onClick={sendNote} disabled={!transcript.trim() || processing || disabled}><Send size={18}/> Send</button>
          </>
        ) : (
          <button type="button" className="voice-inline-stop" onClick={() => void startCapture()} disabled={processing || disabled}><Mic size={17}/> Try again</button>
        )}
      </div>
      {previewUrl && <audio ref={audioRef} src={previewUrl} onEnded={() => setPlaying(false)} onPause={() => setPlaying(false)} preload="metadata"/>}
      {previewUrl && (
        <details className="voice-inline-transcript">
          <summary>Check spoken words {transcript.trim() ? '' : '(required to send)'}</summary>
          <label htmlFor="voice-inline-text">Naija Bites will understand this text:</label>
          <textarea id="voice-inline-text" rows={2} value={transcript} onChange={event => setTranscript(event.target.value)} placeholder="Your spoken words appear here. Edit if needed."/>
        </details>
      )}
      {error && <p className="voice-inline-error" role="alert">{error}</p>}
      <p className="voice-inline-hint">Swipe the handle down or tap <strong>×</strong> to cancel. Audio stays in this browser; the agent receives your spoken words as text.</p>
    </section>
  );
}
