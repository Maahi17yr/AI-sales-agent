'use client';

import { useEffect, useRef } from 'react';
import { AudioLines, Mic, MicOff, Volume2, X } from 'lucide-react';

type Props = { open: boolean; onClose: () => void };

/** Visual-only preview: does not request microphone access or transmit audio. */
export default function VoiceChatPanel({ open, onClose }: Props) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
      if (event.key === 'Tab') {
        event.preventDefault();
        closeRef.current?.focus();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      previous?.focus();
    };
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="voice-backdrop" onMouseDown={event => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section className="voice-dialog" role="dialog" aria-modal="true" aria-labelledby="voice-title" aria-describedby="voice-description">
        <button ref={closeRef} type="button" className="voice-close" onClick={onClose} aria-label="Close voice chat"><X size={21}/></button>
        <div className="voice-eyebrow"><AudioLines size={17}/> NAIJA BITES VOICE</div>
        <h2 id="voice-title">Let's talk food.</h2>
        <p id="voice-description">Your hands-free ordering experience is on its way.</p>
        <div className="voice-orb" aria-hidden="true"><Mic size={52}/></div>
        <div className="voice-wave" aria-hidden="true">{Array.from({ length: 19 }, (_, i) => <span key={i} style={{ height: `${18 + (i * 17 % 48)}px` }}/>)}</div>
        <p className="voice-status">Voice connection not available yet</p>
        <div className="voice-controls">
          <button type="button" disabled aria-label="Microphone not available yet"><MicOff size={22}/></button>
          <button type="button" disabled aria-label="Audio playback not available yet"><Volume2 size={22}/></button>
        </div>
        <p className="voice-footnote">UI preview only · No microphone recording or audio transmission</p>
        <button type="button" className="voice-return" onClick={onClose}>Back to text chat</button>
      </section>
    </div>
  );
}
