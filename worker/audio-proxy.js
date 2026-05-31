/**
 * Cloudflare Worker — Presto
 *
 * fetch handler  : proxy audio MP3 + feed RSS depuis GitHub
 * scheduled      : cron 12h UTC → poste le thread X du jour via OAuth 1.0a
 */

const REPO = "meerkly-00/Presto_Podcast";
const RAW = `https://raw.githubusercontent.com/${REPO}/main`;
const TWITTER_API = "https://api.twitter.com/2/tweets";

// ─── fetch handler (audio proxy existant) ────────────────────────────────────

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (url.pathname === "/feed.xml") {
      const ghUrl = `${RAW}/feed.xml`;
      return proxyTo(request, ghUrl, "application/rss+xml; charset=utf-8", 300);
    }

    const m = url.pathname.match(/^\/audio\/(\d{4}-\d{2}-\d{2})\.mp3$/);
    if (m) {
      const date = m[1];
      return proxyTo(request, `https://github.com/${REPO}/releases/download/${date}/${date}.mp3`);
    }

    const me = url.pathname.match(/^\/audio\/(eco-\d{4}-\d{2}-\d{2})\.mp3$/);
    if (me) {
      const slug = me[1];
      return proxyTo(request, `https://github.com/${REPO}/releases/download/${slug}/${slug}.mp3`);
    }

    return new Response("Not found", { status: 404 });
  },

  // ─── scheduled handler : poster le thread X ────────────────────────────────

  async scheduled(event, env, ctx) {
    const date = new Date().toISOString().slice(0, 10); // YYYY-MM-DD (UTC)
    console.log(`[tweet-cron] starting for ${date}`);

    // Vérifier si le fichier tweet JSON est disponible
    const tweetsUrl = `${RAW}/data/tweets/${date}.json`;
    const tweetsResp = await fetch(tweetsUrl, { cf: { cacheEverything: false } });
    if (!tweetsResp.ok) {
      console.log(`[tweet-cron] no tweets file for ${date} (${tweetsResp.status}), skip`);
      return;
    }

    const { tweets } = await tweetsResp.json();
    if (!tweets || tweets.length === 0) {
      console.log(`[tweet-cron] empty tweet list for ${date}, skip`);
      return;
    }

    console.log(`[tweet-cron] posting ${tweets.length} tweets`);

    let replyToId = null;
    for (let i = 0; i < tweets.length; i++) {
      const id = await postTweet(tweets[i], replyToId, env);
      console.log(`[tweet-cron] tweet ${i + 1}/${tweets.length} → ${id}`);
      replyToId = id;
      if (i < tweets.length - 1) await sleep(2500);
    }

    console.log(`[tweet-cron] thread done → https://x.com/prestopodcast/status/${replyToId}`);
  },
};

// ─── Twitter OAuth 1.0a ──────────────────────────────────────────────────────

async function buildOAuthHeader(method, url, env) {
  const oauthParams = {
    oauth_consumer_key: env.TWITTER_API_KEY,
    oauth_nonce: crypto.randomUUID().replace(/-/g, ""),
    oauth_signature_method: "HMAC-SHA1",
    oauth_timestamp: Math.floor(Date.now() / 1000).toString(),
    oauth_token: env.TWITTER_ACCESS_TOKEN,
    oauth_version: "1.0",
  };

  // Signature base string — pour JSON body, seuls les oauth_* params sont signés
  const sortedPairs = Object.entries(oauthParams)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([k, v]) => `${pct(k)}=${pct(v)}`)
    .join("&");

  const sigBase = `${method.toUpperCase()}&${pct(url)}&${pct(sortedPairs)}`;
  const sigKey = `${pct(env.TWITTER_API_SECRET)}&${pct(env.TWITTER_ACCESS_TOKEN_SECRET)}`;

  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(sigKey),
    { name: "HMAC", hash: "SHA-1" },
    false,
    ["sign"]
  );
  const sigBytes = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(sigBase));
  const signature = btoa(String.fromCharCode(...new Uint8Array(sigBytes)));

  const headerParts = Object.entries({ ...oauthParams, oauth_signature: signature })
    .map(([k, v]) => `${pct(k)}="${pct(v)}"`)
    .join(", ");

  return `OAuth ${headerParts}`;
}

async function postTweet(text, replyToId, env) {
  const authHeader = await buildOAuthHeader("POST", TWITTER_API, env);

  const body = { text };
  if (replyToId) body.reply = { in_reply_to_tweet_id: replyToId };

  const resp = await fetch(TWITTER_API, {
    method: "POST",
    headers: {
      Authorization: authHeader,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });

  if (!resp.ok) {
    const err = await resp.text();
    throw new Error(`Twitter API ${resp.status}: ${err.slice(0, 300)}`);
  }

  const data = await resp.json();
  return data.data.id;
}

// ─── helpers ─────────────────────────────────────────────────────────────────

const pct = (s) => encodeURIComponent(String(s));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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
