/**
 * Cloudflare Worker — Presto
 *
 * fetch handler  : proxy audio MP3 + feed RSS depuis GitHub
 * scheduled      : 8h UTC  → déclenche le briefing GitHub Actions (ponctuel)
 *                  12h/16h/21h30 UTC → poste sur X via OAuth 1.0a
 */

const REPO = "meerkly-00/Presto_Podcast";
const RAW = `https://raw.githubusercontent.com/${REPO}/main`;
const TWITTER_API = "https://api.twitter.com/2/tweets";

// ─── fetch handler (audio proxy existant) ────────────────────────────────────

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // feed.xml servi depuis GitHub (raw), pas depuis Pages : le flux doit
    // rester frais même quand `wrangler pages deploy` échoue (token expiré,
    // panne). Le cache Cloudflare de 5 min limite les invocations du worker.
    if (url.pathname === "/feed.xml") {
      return proxyRaw("feed.xml", "application/rss+xml; charset=utf-8");
    }

    const m = url.pathname.match(/^\/audio\/(\d{4}-\d{2}-\d{2})\.mp3$/);
    if (m) {
      const date = m[1];
      return redirectTo(`https://github.com/${REPO}/releases/download/${date}/${date}.mp3`);
    }

    const me = url.pathname.match(/^\/audio\/(eco-\d{4}-\d{2}-\d{2})\.mp3$/);
    if (me) {
      const slug = me[1];
      return redirectTo(`https://github.com/${REPO}/releases/download/${slug}/${slug}.mp3`);
    }

    return new Response("Not found", { status: 404 });
  },

  // ─── scheduled handler : action selon l'heure (cron) ──────────────────────
  //   0 8  * * *  (4h EDT)    → déclenche briefing.yml sur GitHub Actions
  // ⏸ Volet X en pause (28 août 2026) : les crons ci-dessous sont retirés de
  // wrangler.toml. Le code reste ici pour une réactivation sans réécriture.
  //   0 12 * * *  (8h EDT)    → thread du matin     data/tweets/DATE.json
  //   0 16 * * *  (12h EDT)   → poll de midi        data/tweets/DATE-midi.json
  //   30 21 * * * (17h30 EDT) → contre-programme    data/tweets/DATE-soir.json
  // (heures EDT en été ; décalent d'1h en hiver, sans incidence sur le contenu)

  async scheduled(event, env, ctx) {
    const date = new Date().toISOString().slice(0, 10); // YYYY-MM-DD (UTC)
    const cron = event.cron;
    console.log(`[cron] ${cron} for ${date}`);

    try {
      if (cron === "0 8 * * *") {
        await dispatchBriefing(env);
      } else if (cron === "0 12 * * *") {
        await postThreadFile(`data/tweets/${date}.json`, env);
      } else if (cron === "0 16 * * *") {
        await postSingleFile(`data/tweets/${date}-midi.json`, env);
      } else if (cron === "30 21 * * *") {
        await postSingleFile(`data/tweets/${date}-soir.json`, env);
      } else {
        // Pas de branche par défaut : un cron ajouté par erreur ne doit jamais
        // publier sur X à notre insu.
        console.log(`[cron] ${cron} non reconnu, rien à faire`);
      }
    } catch (e) {
      console.log(`[cron] error: ${e && e.message ? e.message : e}`);
    }
  },
};

// ─── déclencheur du briefing ─────────────────────────────────────────────────

// Les crons GitHub Actions sont livrés avec 20 min à plusieurs heures de retard.
// Les crons Cloudflare, eux, partent à l'heure : on déclenche donc le workflow
// d'ici, pour que l'épisode soit en ligne bien avant 6h heure de l'Est.
// Secret requis : GH_DISPATCH_TOKEN (PAT fine-grained, permission Actions: R/W).
async function dispatchBriefing(env) {
  if (!env.GH_DISPATCH_TOKEN) {
    console.log("[cron] GH_DISPATCH_TOKEN absent — briefing non déclenché");
    return;
  }
  const resp = await fetch(
    `https://api.github.com/repos/${REPO}/actions/workflows/briefing.yml/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GH_DISPATCH_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "presto-worker",
      },
      body: JSON.stringify({ ref: "main" }),
    }
  );
  // 204 = accepté. Tout le reste laisse les crons GitHub prendre le relais.
  console.log(`[cron] dispatch briefing → ${resp.status}`);
  if (!resp.ok) console.log(`[cron] ${(await resp.text()).slice(0, 300)}`);
}

// ─── posteurs ────────────────────────────────────────────────────────────────

async function fetchJson(path) {
  const resp = await fetch(`${RAW}/${path}`, { cf: { cacheEverything: false } });
  if (!resp.ok) {
    console.log(`[cron] no file ${path} (${resp.status}), skip`);
    return null;
  }
  return resp.json();
}

async function postThreadFile(path, env) {
  const data = await fetchJson(path);
  const tweets = data && data.tweets;
  if (!tweets || tweets.length === 0) {
    console.log(`[cron] empty thread ${path}, skip`);
    return;
  }
  console.log(`[cron] posting thread of ${tweets.length} tweets`);
  let replyToId = null;
  for (let i = 0; i < tweets.length; i++) {
    replyToId = await postTweet({ text: tweets[i], replyToId }, env);
    console.log(`[cron] tweet ${i + 1}/${tweets.length} → ${replyToId}`);
    if (i < tweets.length - 1) await sleep(2500);
  }
  console.log(`[cron] thread done → https://x.com/prestopodcast/status/${replyToId}`);
}

async function postSingleFile(path, env) {
  const data = await fetchJson(path);
  if (!data || !data.text) {
    console.log(`[cron] empty single ${path}, skip`);
    return;
  }
  try {
    const id = await postTweet({ text: data.text, poll: data.poll }, env);
    console.log(`[cron] single (${data.kind || "single"}) → https://x.com/prestopodcast/status/${id}`);
  } catch (e) {
    // Fallback : si l'API refuse le poll (tier non supporté), on reposte en
    // texte seul. La question est déjà incluse dans data.text, donc lisible.
    if (data.poll) {
      console.log(`[cron] poll refusé (${e && e.message ? e.message : e}) → repli texte`);
      const id = await postTweet({ text: data.text }, env);
      console.log(`[cron] single repli texte → https://x.com/prestopodcast/status/${id}`);
    } else {
      throw e;
    }
  }
}

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

async function postTweet({ text, replyToId, poll }, env) {
  const authHeader = await buildOAuthHeader("POST", TWITTER_API, env);

  const body = { text };
  if (replyToId) body.reply = { in_reply_to_tweet_id: replyToId };
  if (poll && Array.isArray(poll.options) && poll.options.length >= 2) {
    body.poll = {
      options: poll.options.slice(0, 4),
      duration_minutes: poll.duration_minutes || 1440,
    };
  }

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

async function proxyRaw(path, contentType, maxAge = 300) {
  const resp = await fetch(`${RAW}/${path}`, { cf: { cacheTtl: maxAge, cacheEverything: true } });
  if (!resp.ok) return new Response("Not found", { status: 404 });
  return new Response(resp.body, {
    headers: {
      "Content-Type": contentType,
      "Cache-Control": `public, max-age=${maxAge}`,
      "Access-Control-Allow-Origin": "*",
    },
  });
}

const pct = (s) => encodeURIComponent(String(s));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Redirection plutôt que proxy : le lecteur podcast va chercher les octets
// directement chez GitHub, y compris ses requêtes Range. L'ancien proxy ne
// transmettait pas l'en-tête Range — il retirait donc les ~6 Mo au complet à
// chaque avance ou reprise, et chaque plage demandée comptait comme une
// invocation du worker. Ici on invoque le worker une fois par démarrage.
function redirectTo(target, maxAge = 86400) {
  return new Response(null, {
    status: 302,
    headers: {
      Location: target,
      "Cache-Control": `public, max-age=${maxAge}`,
      "Access-Control-Allow-Origin": "*",
    },
  });
}
