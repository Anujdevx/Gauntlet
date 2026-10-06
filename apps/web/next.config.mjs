/**
 * Next.js configuration.
 *
 * `output: "standalone"` produces a self-contained server bundle, so the runtime
 * Docker image needs neither node_modules nor a build toolchain. That is a
 * meaningful reduction in the frontend's own attack surface.
 *
 * The security headers mirror the API's: the app displays candidate-authored
 * code and runner output, so a restrictive CSP is not decoration here. Monaco
 * needs `worker-src blob:` for its language workers and `unsafe-inline` for
 * Next's hydration scripts, which is why it is not `default-src 'none'` the way
 * the API's is.
 *
 * `poweredByHeader: false` avoids advertising the framework to a scanner that
 * should not find anything interesting.
 */
const nextConfig = {
  output: 'standalone',
  reactStrictMode: true,
  poweredByHeader: false,
  productionBrowserSourceMaps: false,
  eslint: { ignoreDuringBuilds: false },
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          { key: 'Cross-Origin-Opener-Policy', value: 'same-origin' },
          { key: 'Permissions-Policy', value: 'geolocation=(), microphone=(), camera=()' },
          {
            key: 'Content-Security-Policy',
            value: [
              "default-src 'self'",
              "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
              "style-src 'self' 'unsafe-inline'",
              "img-src 'self' data: blob:",
              "font-src 'self' data:",
              "worker-src 'self' blob:",
              // Monaco loads its worker from a blob URL; without this connect-src
              // the editor silently fails to initialise with no error message.
              "connect-src 'self' ws: wss:",
              "frame-ancestors 'none'",
              "base-uri 'self'",
              "object-src 'none'",
            ].join('; '),
          },
        ],
      },
    ];
  },
};

export default nextConfig;
