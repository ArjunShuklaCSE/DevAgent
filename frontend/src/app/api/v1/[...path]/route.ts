/**
 * Same-origin proxy to the API service. The browser only ever talks to the web server,
 * so the API needs no CORS and its address can stay on the internal network. Response
 * bodies are streamed through unchanged, which keeps Server-Sent Events live.
 */
import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

const API_URL = process.env.DEVAGENT_API_INTERNAL_URL ?? "http://localhost:8000";
const FORWARDED_REQUEST_HEADERS = ["accept", "content-type", "last-event-id"];
const FORWARDED_RESPONSE_HEADERS = ["content-type", "cache-control", "x-request-id"];

type Context = { params: Promise<{ path: string[] }> };

export async function proxy(request: NextRequest, { params }: Context): Promise<Response> {
  const { path } = await params;
  const target = new URL(`/api/v1/${path.map(encodeURIComponent).join("/")}`, API_URL);
  target.search = request.nextUrl.search;

  const headers = new Headers();
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value !== null) headers.set(name, value);
  }
  const hasBody = request.method !== "GET" && request.method !== "HEAD";

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.arrayBuffer() : undefined,
      cache: "no-store",
      signal: request.signal, // a closed browser tab closes the upstream SSE stream too
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return Response.json(
      {
        error: { code: "api_unreachable", message: `API request failed: ${message}`, details: {} },
      },
      { status: 502 },
    );
  }

  const responseHeaders = new Headers();
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value !== null) responseHeaders.set(name, value);
  }
  if (responseHeaders.get("content-type")?.startsWith("text/event-stream")) {
    responseHeaders.set("cache-control", "no-cache, no-transform");
    responseHeaders.set("x-accel-buffering", "no");
  }
  return new Response(upstream.body, { status: upstream.status, headers: responseHeaders });
}

export { proxy as GET, proxy as POST, proxy as DELETE };
