/**
 * The reader's service worker (spec 003 §3): installable, and a course opened once
 * keeps working offline.
 *
 *   app shell       precached per build; a new build replaces the cache
 *   catalog.json    network first, cache fallback — current online, browsable offline
 *   courses/*.json  stale-while-revalidate — instant, offline once opened; tells the
 *                   page when the copy it just served turned out to be out of date
 *
 * The site build fills in VERSION and SHELL from READER_FILES (storage/site.py,
 * `service_worker`), so the precache can't drift from what the site ships and any
 * change to a reader file is a new version. The author's server never serves this
 * file: a preview has to show what publishing would write now, not a cached copy.
 *
 * Only the files this site ships are handled. Anything else on the origin (another
 * app on the same localhost port, Google Fonts) goes to the network untouched.
 */

const VERSION = "ae0038959ae9";
const SHELL = ["./", "static/css/app.css", "static/js/reader.js", "static/js/data.js", "static/js/lesson.js", "static/js/rail.js", "static/js/tutor.js", "static/js/flashcards.js", "static/js/markup.js", "static/js/state.js", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png", "icons/icon-maskable-512.png"];

const SHELL_CACHE = `ss-shell-${VERSION}`;
// Not versioned: a new reader still reads the same courses, and dropping them on
// every deploy would take every opened course offline with it.
const DATA_CACHE = "ss-data-v1";

const here = (path) => new URL(path, self.location.href).href;
const BUNDLE = /^courses\/[A-Za-z0-9][A-Za-z0-9_-]*\.course\.json$/;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL_CACHE)
      // "reload" skips the HTTP cache: a host's max-age must not put last deploy's
      // file into this deploy's cache.
      .then((cache) => cache.addAll(SHELL.map((p) => new Request(here(p), { cache: "reload" }))))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(
          names
            .filter((n) => n.startsWith("ss-shell-") && n !== SHELL_CACHE)
            .map((n) => caches.delete(n)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const scope = new URL(self.registration.scope);
  const url = new URL(request.url);
  if (url.origin !== scope.origin || !url.pathname.startsWith(scope.pathname)) return;
  const path = url.pathname.slice(scope.pathname.length);

  if (path === "catalog.json") {
    event.respondWith(networkFirst(request));
  } else if (BUNDLE.test(path)) {
    event.respondWith(staleWhileRevalidate(event));
  } else if (path === "" || path === "index.html") {
    event.respondWith(fromShell(request, here("./")));
  } else if (SHELL.includes(path)) {
    event.respondWith(fromShell(request, here(path)));
  }
});

async function fromShell(request, key) {
  const hit = await caches.match(key, { cacheName: SHELL_CACHE });
  return hit || fetch(request);
}

async function networkFirst(request) {
  const cache = await caches.open(DATA_CACHE);
  try {
    const res = await fetch(request, { cache: "no-cache" });
    if (res.ok) await cache.put(request, res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(request);
    if (hit) return hit;
    throw err;
  }
}

function staleWhileRevalidate(event) {
  const { request } = event;
  const cached = caches.open(DATA_CACHE).then((cache) => cache.match(request));

  const fresh = fetch(request, { cache: "no-cache" }).then(async (res) => {
    if (!res.ok) return res;
    const forCache = res.clone();
    const forCompare = res.clone();
    const cache = await caches.open(DATA_CACHE);
    // Its own lookup, not `cached`: that Response is the one the page is reading, and
    // a body can only be read once.
    const old = await cache.match(request);
    await cache.put(request, forCache);
    if (old) await announceIfNewer(request.url, old, forCompare);
    return res;
  });
  // Offline with a cached copy is the normal case, not an error.
  event.waitUntil(fresh.catch(() => {}));

  return cached.then((hit) => hit || fresh);
}

async function publishedAt(res) {
  try {
    return Number((await res.json()).publishedAt) || 0;
  } catch {
    return 0;
  }
}

/** The page decides whether it's showing the old copy; this only says a newer one exists. */
async function announceIfNewer(url, old, fresh) {
  const [before, after] = await Promise.all([publishedAt(old), publishedAt(fresh)]);
  if (after <= before) return;
  const clients = await self.clients.matchAll({ type: "window" });
  for (const client of clients) client.postMessage({ type: "bundle-updated", url, publishedAt: after });
}
