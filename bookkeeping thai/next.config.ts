import type {NextConfig} from 'next';

const allowedDevOrigins = ['localhost', '127.0.0.1', '127.217.110.96'];
const publicOrigin = process.env.THAI_FRONTEND_ORIGIN?.trim();
if (publicOrigin) {
  // Match the configured tunnel only; changing .env.local updates the allowlist.
  allowedDevOrigins.push(new URL(publicOrigin).hostname);
}

const config: NextConfig = {
  output: 'standalone',
  allowedDevOrigins,
  turbopack: {root: __dirname},
};
export default config;
