'use client';

import { useRef, useState } from 'react';
import { Pause, Play } from 'lucide-react';
import type { VoiceClip } from './voice-chat-panel';

type Props = { clip: VoiceClip; transcript: string };
const formatTime = (seconds: number) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;

export default function VoiceNoteBubble({ clip, transcript }: Props) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [position, setPosition] = useState(0);

  function togglePlayback() {
    const audio = audioRef.current;
    if (!audio) return;
    if (audio.paused) {
      void audio.play().then(() => setPlaying(true)).catch(() => setPlaying(false));
    } else {
      audio.pause();
      setPlaying(false);
    }
  }

  return (
    <div className="chat-voice-note">
      <div className="chat-voice-player">
        <button type="button" className="chat-voice-play" onClick={togglePlayback} aria-label={playing ? 'Pause voice note' : 'Play voice note'}>
          {playing ? <Pause size={19} fill="currentColor"/> : <Play size={19} fill="currentColor"/>}
        </button>
        <div className="chat-voice-wave" aria-hidden="true">
          {Array.from({ length: 26 }, (_, i) => <i key={i} className={i / 26 <= position / Math.max(1, clip.duration) ? 'heard' : ''} style={{ height: `${9 + (i * 17 + i * i * 7) % 26}px` }}/>)}
        </div>
        <span className="chat-voice-time">{formatTime(playing ? Math.floor(position) : clip.duration)}</span>
        <audio ref={audioRef} src={clip.url} preload="metadata" onTimeUpdate={event => setPosition(event.currentTarget.currentTime)} onEnded={() => { setPlaying(false); setPosition(0); }}/>
      </div>
      <details className="chat-voice-transcript">
        <summary>View transcript</summary>
        <p>{transcript}</p>
      </details>
    </div>
  );
}
