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
  const [sendAfterStop, setSendAfterStop] = useState(false);
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

  // Sending while recording stops capture first. The audio and final transcript
  // are committed only after MediaRecorder has produced the playable clip.
  useEffect(() => {
    if (!sendAfterStop || recording || processing || !previewUrl || disabled) return;
    if (!transcript.trim()) return; // Allow recognition to finish or the user to edit.
    setSendAfterStop(false);
    sendNote();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sendAfterStop, recording, processing, previewUrl, transcript, disabled]);

  function handlePrimaryAction() {
    if (recording) {
      setSendAfterStop(true);
      stopCapture();
    } else if (previewUrl) {
      sendNote();
    } else {
      void startCapture();
    }
  }

  if (!open) return null;
  return (
    <div className="voice-composer-wrap">
      <section className="composer voice-inline-panel" aria-label="Voice note composer">
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
          disabled={processing || disabled || sendAfterStop || (!recording && !!previewUrl && !transcript.trim())}
          aria-label={recording ? 'Finish and send voice note' : previewUrl ? 'Send voice note' : 'Try recording again'}
          title={recording ? 'Finish and send' : previewUrl ? 'Send voice note' : 'Record'}>
          {processing || sendAfterStop ? <span className="voice-inline-sending">···</span> : <Send size={21}/>}
        </button>
      </section>
      {previewUrl && <audio ref={audioRef} src={previewUrl} onEnded={() => setPlaying(false)} onPause={() => setPlaying(false)} preload="metadata"/>}
      {(error || (previewUrl && !transcript.trim())) && (
        <div className="voice-inline-help" role="status">
          {error && <span className="voice-inline-error">{error}</span>}
          {previewUrl && !transcript.trim() && <label htmlFor="voice-inline-text">Enter what you said so Naija Bites can understand your note:</label>}
          {previewUrl && !transcript.trim() && <input id="voice-inline-text" value={transcript} onChange={event => setTranscript(event.target.value)} placeholder="Your spoken words"/>}
        </div>
      )}
      {previewUrl && transcript.trim() && (
        <details className="voice-inline-transcript">
          <summary>Edit transcript</summary>
          <label htmlFor="voice-inline-edit">Words sent to Naija Bites</label>
          <textarea id="voice-inline-edit" rows={2} value={transcript} onChange={event => setTranscript(event.target.value)}/>
        </details>
      )}
      <span className="sr-only">Voice notes are stored in this browser session; only the transcript is sent to the AI.</span>
    </div>
  );
}
