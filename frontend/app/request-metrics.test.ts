/** Isolated, executable request-count tests. Run with:
 * npx tsc --target es2020 --module commonjs --outDir /tmp/naija-request-test app/request-metrics.ts
 * node -e "require('/tmp/naija-request-test/request-metrics.js')"
 * This file is also checked by the production TypeScript build.
 */
import { beginJourney, currentJourney, recordRequest, finishJourney, classifyRequest } from './request-metrics';

export function checkRequestMetrics(): void {
  beginJourney('chat_turn');
  recordRequest('auth', false);
  recordRequest('agent', false);
  recordRequest('checkout', true);
  const chat = finishJourney();
  if (chat.journey !== 'chat_turn' || chat.requests !== 3 || chat.errors !== 1
      || chat.byStage.auth !== 1 || chat.byStage.checkout !== 1) {
    throw new Error('Incorrect chat-turn baseline');
  }
  beginJourney('checkout');
  recordRequest('auth', false);
  recordRequest('checkout', false);
  const checkout = finishJourney();
  if (checkout.requests !== 2 || checkout.byStage.auth !== 1) throw new Error('Incorrect checkout baseline');
  beginJourney('payment');
  recordRequest('payment', false);
  if (finishJourney().byStage.payment !== 1) throw new Error('Incorrect payment baseline');
  if (currentJourney() !== null || classifyRequest('/payments/verify') !== 'payment') {
    throw new Error('Request metrics cleanup failed');
  }
}
