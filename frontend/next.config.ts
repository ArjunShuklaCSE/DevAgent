import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone", // minimal server bundle for the `web` Docker image
  poweredByHeader: false,
};

export default nextConfig;
