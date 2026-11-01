'use client';

import { useEffect, useRef, useState } from 'react';
import { AudioLines, Mic, Play, Send, Square, Trash2, X } from 'lucide-react';

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
    closeRef.current?.focus();
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

  function sendNote() {
    if (recording || processing || disabled || !previewUrl || !transcript.trim()) return;
    transferredRef.current = previewUrl;
    onSend(transcript.trim(), { url: previewUrl, duration: Math.max(1, elapsed) });
    onClose();
  }

  if (!open) return null;
  return (
    <div className="voice-backdrop" onMouseDown={event => { if (event.target === event.currentTarget && !recording) onClose(); }}>
      <section className="voice-dialog voice-note-dialog" role="dialog" aria-modal="true" aria-labelledby="voice-title" aria-describedby="voice-description">
        <button ref={closeRef} type="button" className="voice-close" onClick={onClose} aria-label="Close voice note"><X size={20}/></button>
        <div className="voice-eyebrow"><AudioLines size={17}/> NAIJA BITES VOICE NOTE</div>
        <h2 id="voice-title">Say it. We'll handle it.</h2>
        <p id="voice-description">Record a voice note and send it as an instruction to Naija Bites.</p>

        <div className={`voice-note-preview ${recording ? 'is-recording' : ''}`}>
          <button className="voice-note-preview-play" type="button" onClick={() => {
            if (!audioRef.current) return;
            if (playing) { audioRef.current.pause(); setPlaying(false); }
            else void audioRef.current.play().then(() => setPlaying(true)).catch(() => setError('Could not play this recording.'));
          }} disabled={!previewUrl || recording} aria-label={playing ? 'Pause voice note' : 'Play voice note'}>
            {recording ? <Square size={17}/> : previewUrl ? <Play size={20} fill="currentColor"/> : <Mic size={20}/>}
          </button>
          <div className="voice-note-preview-wave" aria-hidden="true">
            {Array.from({ length: 26 }, (_, i) => <i key={i} style={{ height: `${9 + (i * 17 + i * i * 7) % 26}px` }}/>)}
          </div>
          <span className="voice-note-time">{formatTime(elapsed)}</span>
          {previewUrl && <audio ref={audioRef} src={previewUrl} onEnded={() => setPlaying(false)} onPause={() => setPlaying(false)} preload="metadata"/>}
        </div>
        <p className="voice-status" role="status">{recording ? 'Recording… tap stop when finished' : processing ? 'Preparing voice note…' : previewUrl ? 'Voice note ready to send' : 'Tap the microphone to start'}</p>
        <div className="voice-note-actions">
          {recording ? (
            <button type="button" className="voice-note-record is-active" onClick={stopCapture}><Square size={18}/> Stop recording</button>
          ) : (
            <button type="button" className="voice-note-record" onClick={() => void startCapture()} disabled={processing || disabled}><Mic size={18}/> {previewUrl ? 'Record again' : 'Record voice note'}</button>
          )}
          {previewUrl && !recording && <button type="button" className="voice-note-discard" onClick={discard} aria-label="Discard voice note"><Trash2 size={19}/></button>}
        </div>
        <label className="voice-transcript-label" htmlFor="voice-transcript">What Naija Bites will understand</label>
        <textarea id="voice-transcript" className="voice-transcript" rows={2} value={transcript} onChange={event => setTranscript(event.target.value)} placeholder="Your spoken instruction appears here…" disabled={recording || processing || disabled}/>
        <button className="voice-send" type="button" onClick={sendNote} disabled={!previewUrl || !transcript.trim() || recording || processing || disabled}><Send size={18}/> Send voice note</button>
        {error && <p className="voice-error" role="alert">{error}</p>}
        <p className="voice-footnote">The agent acts on the transcript. Audio playback stays in this browser session; voice recordings are not uploaded or saved to cloud history. Browser speech recognition may use your browser provider.</p>
      </section>
    </div>
  );
}
