// Stream the AG-UI response directly. Generic Next.js rewrites can buffer
// upstream SSE until completion, making otherwise fast model deltas arrive in
// one giant burst. Only /agent uses this narrowly scoped streaming proxy.
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

function backendOrigin(): string {
  const configured = process.env.NEXT_BACKEND_URL?.trim() ||
    (process.env.NODE_ENV === 'development' ? 'http://127.0.0.1:8000' : '');
  if (!configured) throw new Error('NEXT_BACKEND_URL is not configured.');
  const url = new URL(configured);
  const localHttp = process.env.NODE_ENV === 'development' &&
    url.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname);
  if ((!localHttp && url.protocol !== 'https:') ||
      url.origin !== configured.replace(/\/$/, '') ||
      url.username || url.password || url.pathname !== '/' ||
      url.search || url.hash) {
    throw new Error('NEXT_BACKEND_URL must be an HTTPS origin.');
  }
  return url.origin;
}

export async function POST(request: Request) {
  try {
    const target = backendOrigin() + '/agent';
    const headers = new Headers({
      'Content-Type': request.headers.get('content-type') || 'application/json',
      'Accept': request.headers.get('accept') || 'text/event-stream',
    });
    // The API authenticates exclusively using this HttpOnly session cookie.
    // Forward the browser's actual Origin for the existing CSRF restriction.
    for (const name of ['cookie', 'origin', 'x-request-id']) {
      const value = request.headers.get(name);
      if (value) headers.set(name, value);
    }
    const upstream = await fetch(target, {
      method: 'POST',
      headers,
      body: await request.arrayBuffer(),
      cache: 'no-store',
      redirect: 'manual',
      signal: request.signal,
    });

    const responseHeaders = new Headers({
      'Content-Type': upstream.headers.get('content-type') || 'application/json',
      'Cache-Control': 'no-cache, no-transform',
      'X-Accel-Buffering': 'no',
    });
    const correlationId = upstream.headers.get('x-request-id');
    if (correlationId) responseHeaders.set('X-Request-ID', correlationId);
    // Never call response.text() or response.json() here: both would buffer
    // the complete stream. Keep cancellation and SSE framing intact.
    return new Response(upstream.body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch {
    return Response.json({
      detail: 'The assistant is temporarily unavailable. Please retry.',
      code: 'agent_proxy_unavailable',
    }, {status: 502, headers: {'Cache-Control': 'no-store'}});
  }
}
