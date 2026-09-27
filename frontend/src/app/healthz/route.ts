/** Liveness for the `web` container healthcheck; independent of the API. */
export function GET(): Response {
  return Response.json({ status: "ok" });
}
