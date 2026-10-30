import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  async rewrites() {
    const backend = process.env.NEXT_BACKEND_URL?.replace(/\/$/, '');
    if (!backend) {
      // Local development can use NEXT_PUBLIC_AGUI_URL in .env.local instead.
      return [];
    }
    if (!/^https:\/\//.test(backend)) {
      throw new Error('NEXT_BACKEND_URL must be an HTTPS backend URL.');
    }
    return [{ source: '/api/backend/:path*', destination: `${backend}/:path*` }];
  },
};

export default nextConfig;
