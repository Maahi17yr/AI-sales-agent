/** In-memory, privacy-safe request counters: never record URLs, bodies or cookies. */
export type Journey = 'chat_turn' | 'checkout' | 'payment';
export type RequestStage = 'agent' | 'checkout' | 'payment' | 'auth' | 'chats' | 'menu' | 'cart' | 'other';

type Observation = { stage: RequestStage; failed: boolean };
const entries: Observation[] = [];
let journey: Journey | null = null;

export function beginJourney(kind: Journey): void {
  journey = kind;
  entries.length = 0;
}

export function currentJourney(): Journey | null { return journey; }

export function recordRequest(stage: RequestStage, failed: boolean): void {
  if (journey) entries.push({ stage, failed });
}

export function finishJourney(): {
  journey: Journey | null; requests: number; errors: number;
  byStage: Partial<Record<RequestStage, number>>;
} {
  const byStage: Partial<Record<RequestStage, number>> = {};
  for (const entry of entries) byStage[entry.stage] = (byStage[entry.stage] ?? 0) + 1;
  const result = {journey, requests: entries.length,
                  errors: entries.filter(e => e.failed).length, byStage};
  journey = null;
  entries.length = 0;
  return result;
}

export function classifyRequest(path: string): RequestStage {
  if (path === '/agent') return 'agent';
  if (path.startsWith('/checkout')) return 'checkout';
  if (path.startsWith('/payments')) return 'payment';
  if (path.startsWith('/auth')) return 'auth';
  if (path.startsWith('/chats')) return 'chats';
  if (path.startsWith('/menu')) return 'menu';
  if (path.startsWith('/cart')) return 'cart';
  return 'other';
}
