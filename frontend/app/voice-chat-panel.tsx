'use client';

import { useEffect, useRef, useState } from 'react';
import { AudioLines, LoaderCircle, Mic, MicOff, Send, Square, Volume2, VolumeX, X } from 'lucide-react';

type SpeechResult = { transcript: string };
type SpeechResultEvent = {
  resultIndex: number;
  results: ArrayLike<{ isFinal: boolean; [index: number]: SpeechResult }>;
};
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
  onSend: (text: string) => Promise<string | undefined>;
  disabled: boolean;
};

const recognitionError = (code: string) => {
  if (code === 'not-allowed' || code === 'service-not-allowed') return 'Microphone access was denied. Allow microphone access in your browser settings or use text chat.';
  if (code === 'no-speech') return 'No speech was detected. Please try again.';
  if (code === 'audio-capture') return 'No working microphone was found.';
  if (code === 'network') return 'Speech recognition could not connect. You can still type your message.';
  return 'Speech recognition stopped. Please try again or type your message.';
};

export default function VoiceChatPanel({ open, onClose, onSend, disabled }: Props) {
  const [supported, setSupported] = useState(false);
  const [transcript, setTranscript] = useState('');
  const [listening, setListening] = useState(false);
  const [sending, setSending] = useState(false);
  const [muted, setMuted] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [lastReply, setLastReply] = useState('');
  const [error, setError] = useState('');
  const recognizerRef = useRef<SpeechRecognizer | null>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLElement>(null);
  const openRef = useRef(open);
  const closeHandlerRef = useRef(onClose);
  openRef.current = open;
  closeHandlerRef.current = onClose;

  useEffect(() => {
    const speechWindow = window as SpeechWindow;
    setSupported(Boolean(speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition));
  }, []);

  function stopPlayback() {
    if (typeof window !== 'undefined' && 'speechSynthesis' in window) window.speechSynthesis.cancel();
    setSpeaking(false);
  }

  function stopListening(abort = false) {
    const recognizer = recognizerRef.current;
    if (!recognizer) return;
    if (abort) recognizer.abort();
    else recognizer.stop();
    if (abort) {
      recognizerRef.current = null;
      setListening(false);
    }
  }

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') closeHandlerRef.current();
      if (event.key !== 'Tab' || !dialogRef.current) return;
      const focusables = Array.from(dialogRef.current.querySelectorAll<HTMLButtonElement | HTMLTextAreaElement>('button:not(:disabled), textarea:not(:disabled)'));
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      recognizerRef.current?.abort();
      recognizerRef.current = null;
      if ('speechSynthesis' in window) window.speechSynthesis.cancel();
      previous?.focus();
    };
  }, [open]);

  function startListening() {
    if (disabled || sending || listening) return;
    const speechWindow = window as SpeechWindow;
    const Constructor = speechWindow.SpeechRecognition || speechWindow.webkitSpeechRecognition;
    if (!Constructor) {
      setError('Speech recognition is not supported by this browser. Please type your message instead.');
      return;
    }
    stopPlayback();
    setError('');
    setTranscript('');
    const recognizer = new Constructor();
    recognizerRef.current = recognizer;
    recognizer.lang = 'en-NG';
    recognizer.continuous = true;
    recognizer.interimResults = true;
    recognizer.onresult = (event) => {
      let text = '';
      for (let i = 0; i < event.results.length; i++) text += event.results[i][0]?.transcript ?? '';
      setTranscript(text.trim());
    };
    recognizer.onerror = (event) => {
      setError(recognitionError(event.error));
      setListening(false);
    };
    recognizer.onend = () => {
      if (recognizerRef.current === recognizer) recognizerRef.current = null;
      setListening(false);
    };
    try {
      recognizer.start();
      setListening(true);
    } catch {
      recognizerRef.current = null;
      setListening(false);
      setError('Could not start the microphone. Please try again.');
    }
  }

  function speak(text: string) {
    if (muted || !openRef.current || !('speechSynthesis' in window)) return;
    stopPlayback();
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = 'en-NG';
    utterance.rate = 1;
    const voices = window.speechSynthesis.getVoices();
    utterance.voice = voices.find(voice => voice.lang.toLowerCase() === 'en-ng')
      ?? voices.find(voice => voice.lang.toLowerCase().startsWith('en-'))
      ?? null;
    utterance.onstart = () => setSpeaking(true);
    utterance.onend = () => setSpeaking(false);
    utterance.onerror = () => setSpeaking(false);
    window.speechSynthesis.speak(utterance);
  }

  async function submit() {
    const text = transcript.trim();
    if (!text || disabled || sending || listening) return;
    setSending(true);
    setError('');
    stopPlayback();
    try {
      const reply = await onSend(text);
      if (!openRef.current) return;
      if (reply?.trim()) {
        setLastReply(reply);
        setTranscript('');
        speak(reply);
      } else {
        setError('The agent could not complete that message. Check the text conversation for details.');
      }
    } catch {
      if (openRef.current) setError('Unable to send the message. Please try again.');
    } finally {
      setSending(false);
    }
  }

  if (!open) return null;
  return (
    <div className="voice-backdrop" onMouseDown={event => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section ref={dialogRef} className="voice-dialog" role="dialog" aria-modal="true" aria-labelledby="voice-title" aria-describedby="voice-description">
        <button ref={closeRef} type="button" className="voice-close" onClick={onClose} aria-label="Close voice chat"><X size={21}/></button>
        <div className="voice-eyebrow"><AudioLines size={17}/> NAIJA BITES VOICE</div>
        <h2 id="voice-title">Let's talk food.</h2>
        <p id="voice-description">Tap the microphone, speak your order, review the words, then send.</p>
        <div className={`voice-orb ${listening ? 'voice-orb--listening' : ''}`} aria-hidden="true">
          {listening ? <AudioLines size={52}/> : <Mic size={52}/>}
        </div>
        <div className="voice-wave" aria-hidden="true">{Array.from({ length: 19 }, (_, i) => <span key={i} style={{ height: `${18 + (i * 17 % 48)}px` }}/>)}</div>
        <p className="voice-status" role="status" aria-live="polite">
          {listening ? 'Listening… tap Stop when finished' : sending || disabled ? 'Waiting for Naija Bites…' : speaking ? 'Speaking reply…' : 'Ready to take your order'}
        </p>
        <div className="voice-controls">
          <button type="button" onClick={() => listening ? stopListening() : startListening()} disabled={sending || disabled || !supported} aria-label={listening ? 'Stop listening' : 'Start voice input'} title={listening ? 'Stop listening' : 'Start voice input'}>
            {listening ? <Square size={21}/> : supported ? <Mic size={23}/> : <MicOff size={23}/>}
          </button>
          <button type="button" onClick={() => { setMuted(!muted); stopPlayback(); }} aria-label={muted ? 'Unmute voice replies' : 'Mute voice replies'} title={muted ? 'Unmute replies' : 'Mute replies'}>
            {muted ? <VolumeX size={23}/> : <Volume2 size={23}/>}
          </button>
          <button type="button" onClick={() => speaking ? stopPlayback() : speak(lastReply)} disabled={!lastReply || muted} aria-label={speaking ? 'Stop speaking' : 'Replay last reply'} title={speaking ? 'Stop speaking' : 'Replay last reply'}>
            {speaking ? <Square size={21}/> : <AudioLines size={23}/>}
          </button>
        </div>
        <label className="voice-transcript-label" htmlFor="voice-transcript">Your message (edit before sending)</label>
        <textarea id="voice-transcript" className="voice-transcript" rows={3} value={transcript} onChange={event => setTranscript(event.target.value)} placeholder="Your spoken words appear here. You can also type…" disabled={listening || sending || disabled}/>
        <button className="voice-send" type="button" onClick={() => void submit()} disabled={!transcript.trim() || listening || sending || disabled}>
          {sending ? <LoaderCircle size={18} className="spin"/> : <Send size={18}/>} Send to Naija Bites
        </button>
        {error && <p className="voice-error" role="alert">{error}</p>}
        {!supported && <p className="voice-footnote">Microphone transcription isn't supported here. You can type above or use text chat.</p>}
        <p className="voice-footnote">Audio isn't saved by Naija Bites. Browser speech recognition may use your browser provider's service.</p>
        <button type="button" className="voice-return" onClick={onClose}>Back to text chat</button>
      </section>
    </div>
  );
}
