/**
 * Cloudflare Worker — Presto audio proxy
 *
 * Route /audio/<date>.mp3 vers la release GitHub correspondante.
 * URLs publiques propres : https://prestopodcast.online/audio/2026-05-28.mp3
 *
 * Mapping :
 *   /audio/2026-05-28.mp3 -> github.com/meerkly-00/briefing-matinal/releases/download/2026-05-28/2026-05-28.mp3
 *
 * Cloudflare cache automatiquement les MP3 (CF-Cache-Status: HIT).
 */

const REPO = "meerkly-00/Presto_Podcast";

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // /feed.xml — flux RSS principal (proxié depuis GitHub)
    if (url.pathname === "/feed.xml") {
      const ghUrl = `https://raw.githubusercontent.com/${REPO}/main/feed.xml`;
      return proxyTo(request, ghUrl, "application/rss+xml; charset=utf-8", 300);
    }

    // /audio/YYYY-MM-DD.mp3 — épisodes Presto
    const m = url.pathname.match(/^\/audio\/(\d{4}-\d{2}-\d{2})\.mp3$/);
    if (m) {
      const date = m[1];
      const ghUrl = `https://github.com/${REPO}/releases/download/${date}/${date}.mp3`;
      return proxyTo(request, ghUrl);
    }

    // /audio/eco-YYYY-MM-DD.mp3 — anciens épisodes Éco (transition)
    const me = url.pathname.match(/^\/audio\/(eco-\d{4}-\d{2}-\d{2})\.mp3$/);
    if (me) {
      const slug = me[1];
      const ghUrl = `https://github.com/${REPO}/releases/download/${slug}/${slug}.mp3`;
      return proxyTo(request, ghUrl);
    }

    return new Response("Not found", { status: 404 });
  },
};

async function proxyTo(request, target, contentType = null, maxAge = 86400) {
  const upstream = await fetch(target, {
    method: request.method,
    headers: { "User-Agent": "Presto-Proxy/1.0" },
    redirect: "follow",
  });
  const headers = new Headers(upstream.headers);
  headers.set("Cache-Control", `public, max-age=${maxAge}`);
  headers.set("Access-Control-Allow-Origin", "*");
  if (contentType) headers.set("Content-Type", contentType);
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers,
  });
}
