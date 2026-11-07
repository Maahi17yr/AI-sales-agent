/** Independent, in-memory measurements; never record paths, payloads or identities. */
export type Journey = 'chat_turn' | 'checkout' | 'payment';
export type RequestStage = 'agent' | 'checkout' | 'payment' | 'auth' | 'chats' | 'menu' | 'cart' | 'other';
type Counts = { requests: number; errors: number; cancellations: number;
  streamErrors: number; byStage: Partial<Record<RequestStage, number>> };
type RecordState = Counts & { journey: Journey };
const journeys = new Map<string, RecordState>();
let nextJourney = 0;
const background: Counts = { requests: 0, errors: 0, cancellations: 0, streamErrors: 0, byStage: {} };

function empty(kind: Journey): RecordState {
  return { journey: kind, requests: 0, errors: 0, cancellations: 0,
    streamErrors: 0, byStage: {} };
}

export function beginJourney(kind: Journey): string {
  const id = String(++nextJourney);
  journeys.set(id, empty(kind));
  return id;
}

export function recordRequest(stage: RequestStage, failed: boolean, journeyId?: string): void {
  const item = (journeyId && journeys.get(journeyId)) || background;
  item.requests += 1;
  if (failed) item.errors += 1;
  item.byStage[stage] = (item.byStage[stage] ?? 0) + 1;
}

export function recordStreamOutcome(journeyId: string, outcome: 'error' | 'cancelled'): void {
  const item = journeys.get(journeyId);
  if (!item) return;
  if (outcome === 'error') { item.streamErrors += 1; item.errors += 1; }
  else item.cancellations += 1;
}

export function finishJourney(id: string): RecordState | null {
  const item = journeys.get(id);
  if (!item) return null;
  journeys.delete(id);
  return { ...item, byStage: { ...item.byStage } };
}

export function backgroundSnapshot(): Counts {
  return { ...background, byStage: { ...background.byStage } };
}

export function resetBackgroundForTest(): void {
  background.requests = 0;
  background.errors = 0;
  background.cancellations = 0;
  background.streamErrors = 0;
  background.byStage = {};
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
