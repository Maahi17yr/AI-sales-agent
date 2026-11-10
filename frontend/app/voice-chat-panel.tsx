'use client';

import { useEffect, useRef, useState } from 'react';
import { Mic, Play, Send, Square, Trash2 } from 'lucide-react';
import { recordingToWavBase64 } from './voice-audio';

export type VoiceClip = { url: string; duration: number };

type Props = {
  open: boolean;
  onClose: () => void;
  onSend: (transcript: string, clip: VoiceClip) => void;
  disabled: boolean;
  apiFetch: (path: string, init?: RequestInit) => Promise<Response>;
};

const formatTime = (seconds: number) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;

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
  const transcriptRef = useRef('');
  const recordingRef = useRef(false);
  const uploadRef = useRef<AbortController | null>(null);
  const recordingBlob = useRef<Blob | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
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
    sendingRef.current = false;
    setTranscribing(false);
    uploadRef.current?.abort();
    setSendAfterStop(false);
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
    recordingBlob.current = null;
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
        settleRecognition.current();
      window.removeEventListener('keydown', onKeyDown);
      if (timerRef.current) clearInterval(timerRef.current);
      recordingBlob.current = null;
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
        const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
        recordingBlob.current = blob;
        const url = URL.createObjectURL(blob);
        previewUrlRef.current = url;
        setPreviewUrl(url);
      };
      recorder.start(250);
      startedRef.current = Date.now();
      setRecording(true);
      recordingRef.current = true;
      timerRef.current = setInterval(() => {
        const seconds = Math.min(90, Math.floor((Date.now() - startedRef.current) / 1000));
        setElapsed(seconds);
        if (seconds >= 90) stopCapture();
      }, 250);

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
    try {
      const blob = recordingBlob.current;
      if (!blob) throw new Error('No recording is available. Please record again.');
      const controller = new AbortController();
      uploadRef.current = controller;
      const timeout = window.setTimeout(() => controller.abort(), 30000);
      let text = transcriptRef.current.trim();
      try {
        if (!text) {
          const audio_base64 = await recordingToWavBase64(blob);
          if (closedRef.current || controller.signal.aborted) return;
          const response = await apiFetch('/voice/transcribe', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ audio_base64 }), signal: controller.signal,
          });
          const result = await response.json();
          if (!response.ok) throw new Error(
            typeof result.detail === 'string' ? result.detail : 'Could not transcribe this recording. Try Send again.'
          );
          text = typeof result.transcript === 'string' ? result.transcript.trim() : '';
          if (!text) throw new Error('No speech was detected. You can play this recording or record again.');
          updateTranscript(text);
        }
      } finally { window.clearTimeout(timeout); }
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
      releaseStream();
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
        {!recording && previewUrl && !transcript.trim() && (
          <button type="button" className="voice-inline-stop" onClick={() => void startCapture()}
            disabled={transcribing} aria-label="Record voice note again" title="Record again">
            <Mic size={18}/>
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
      {transcribing && <span className="voice-inline-help" role="status">Transcribing and sending voice note…</span>}
      {error && <div className="voice-inline-help voice-inline-error" role="alert">{error}</div>}
      {previewUrl && transcript.trim() && (
        <details className="voice-inline-transcript">
          <summary>Edit transcript</summary>
          <label htmlFor="voice-inline-edit">Words sent to Naija Bites</label>
          <textarea id="voice-inline-edit" rows={2} value={transcript} onChange={event => updateTranscript(event.target.value)}/>
        </details>
      )}
      <span className="sr-only">Your completed recording is sent securely for transcription. Only the resulting text is sent to the ordering assistant.</span>
    </div>
  );
}
