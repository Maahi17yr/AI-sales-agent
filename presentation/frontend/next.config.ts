import type { NextConfig } from 'next';

// Browser requests remain same-origin; Next.js forwards /api/backend/* to FastAPI.
// NEXT_BACKEND_URL is server-only. Do not expose it through NEXT_PUBLIC_.
const isDevelopment = process.env.NODE_ENV === 'development';
const backend = (process.env.NEXT_BACKEND_URL?.trim() ||
  (isDevelopment ? 'http://127.0.0.1:8000' : '')).replace(/\/$/, '');

if (!backend) {
  throw new Error('NEXT_BACKEND_URL is required outside local development (use the HTTPS FastAPI origin).');
}

let target: URL;
try {
  target = new URL(backend);
} catch {
  throw new Error('NEXT_BACKEND_URL must be a valid absolute URL.');
}

const localHttp = isDevelopment &&
  target.protocol === 'http:' &&
  ['localhost', '127.0.0.1', '[::1]'].includes(target.hostname);

if ((target.protocol !== 'https:' && !localHttp) || target.origin !== backend) {
  throw new Error('NEXT_BACKEND_URL must be an HTTPS origin (HTTP localhost is allowed only in development).');
}

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: '/api/backend/:path*', destination: `${backend}/:path*` }];
  },
};

export default nextConfig;
