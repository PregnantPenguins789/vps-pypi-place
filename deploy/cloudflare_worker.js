// Cloudflare Worker — routes sovereignmail.org/pypiplace to the Oracle micro dashboard.
// Deploy at: Cloudflare dashboard → Workers & Pages → Create Worker → paste this → Save.
// Then add route: sovereignmail.org/pypiplace* → this worker.
//
// BACKEND_URL: the Oracle micro instance IP serving the PyPI Place dashboard.
const BACKEND_URL = "http://129.153.15.163";

export default {
  async fetch(request) {
    const url = new URL(request.url);

    if (!url.pathname.startsWith("/pypiplace")) {
      // Not our path — pass through to origin unchanged.
      return fetch(request);
    }

    // Strip /pypiplace prefix before forwarding to backend.
    const backendPath = url.pathname.replace(/^\/pypiplace\/?/, "/") || "/";
    const backendURL = BACKEND_URL + backendPath + (url.search || "");

    const response = await fetch(backendURL, {
      method: request.method,
      headers: {
        "X-Forwarded-For": request.headers.get("CF-Connecting-IP") || "",
        "X-Forwarded-Host": url.hostname,
      },
    });

    // Clone response so we can modify headers.
    const newResponse = new Response(response.body, response);
    newResponse.headers.set("X-Served-By", "pypi-place-oracle-micro");
    // Allow Cloudflare to cache static content briefly.
    if (response.headers.get("Content-Type")?.includes("text/html")) {
      newResponse.headers.set("Cache-Control", "public, max-age=60");
    }

    return newResponse;
  },
};
