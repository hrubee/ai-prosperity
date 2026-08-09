/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [
      {
        source: '/api/vol2b2t/:path*',
        destination: 'http://127.0.0.1:8000/vol2b2t/:path*',
      },
    ];
  },
};

export default nextConfig;
