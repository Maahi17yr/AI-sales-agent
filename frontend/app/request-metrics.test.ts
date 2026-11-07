import {
  beginJourney, recordRequest, recordStreamOutcome, finishJourney,
  backgroundSnapshot, resetBackgroundForTest, classifyRequest,
} from './request-metrics';

function assert(condition: boolean, label: string) {
  if (!condition) throw new Error(label);
}

export function checkRequestMetrics(): void {
  resetBackgroundForTest();
  const chat = beginJourney('chat_turn');
  recordRequest('auth', false, chat);
  const checkout = beginJourney('checkout');
  recordRequest('auth', false, checkout);
  recordRequest('checkout', false, checkout);
  recordRequest('agent', false, chat);
  recordStreamOutcome(chat, 'error');
  recordRequest('chats', true);  // background request, not part of either journey

  const checkoutEnd = finishJourney(checkout);
  assert(checkoutEnd?.requests === 2 && checkoutEnd.errors === 0 &&
         checkoutEnd.byStage.auth === 1, 'Overlapping checkout lost counts');
  const chatEnd = finishJourney(chat);
  assert(chatEnd?.requests === 2 && chatEnd.streamErrors === 1 &&
         chatEnd.errors === 1 && chatEnd.byStage.agent === 1, 'Overlapping chat lost counts');
  assert(finishJourney(chat) === null, 'Finished journey not removed');
  assert(backgroundSnapshot().requests === 1 &&
         backgroundSnapshot().errors === 1, 'Background request lost');

  const payment = beginJourney('payment');
  recordRequest('payment', false, payment);
  recordStreamOutcome(payment, 'cancelled');
  const end = finishJourney(payment);
  assert(end?.requests === 1 && end.cancellations === 1 &&
         end.errors === 0, 'Cancellation was treated as provider failure');
  assert(classifyRequest('/payments/verify') === 'payment', 'Incorrect stage classification');
}
