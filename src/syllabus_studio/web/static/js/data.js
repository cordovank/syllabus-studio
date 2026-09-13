/**
 * The reader's only way to data (spec 003).
 *
 * Reads catalog.json and course bundles over plain fetch, and keeps progress in
 * the browser. It never calls /api/v1 — the reader runs from static files on any
 * host. The Studio talks to the server through api.js instead.
 *
 * `createData` takes its fetch and storage as arguments so it runs under
 * `node --test` with no browser.
 */

const PREFIX = "ss:v1:";
const LIBRARY_MAX = 20;

export class DataError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

/** localStorage, or null where it's unavailable or throws on access (private modes). */
export function browserStorage() {
  try {
    const s = globalThis.localStorage;
    if (!s) return null;
    const probe = `${PREFIX}probe`;
    s.setItem(probe, "1");
    s.removeItem(probe);
    return s;
  } catch {
    return null;
  }
}

/**
 * @param {object} opts
 * @param {string} opts.catalogUrl  absolute URL of catalog.json; bundle URLs resolve against it
 * @param {Function} [opts.fetch]
 * @param {Storage|null} [opts.storage]  null: progress lives in memory for this page only
 * @param {Function} [opts.onStorageError]  called once, the first time progress can't be saved
 */
export function createData({ catalogUrl, fetch = globalThis.fetch, storage = null, onStorageError = () => {} }) {
  let catalogPromise = null;
  const bundles = new Map(); // courseId -> Promise<bundle>
  const memory = new Map(); // key -> string, when storage is missing or failing
  let warned = false;

  function warnOnce(e) {
    if (warned) return;
    warned = true;
    onStorageError(e);
  }

  function read(key) {
    if (memory.has(key)) return memory.get(key);
    if (!storage) return null;
    try {
      return storage.getItem(PREFIX + key);
    } catch (e) {
      warnOnce(e);
      return null;
    }
  }

  function write(key, value) {
    memory.set(key, value); // the page keeps working even if the browser won't save
    if (!storage) {
      warnOnce(new Error("no storage"));
      return false;
    }
    try {
      storage.setItem(PREFIX + key, value);
      return true;
    } catch (e) {
      warnOnce(e);
      return false;
    }
  }

  function readJson(key, fallback) {
    const raw = read(key);
    if (!raw) return fallback;
    try {
      return JSON.parse(raw);
    } catch {
      return fallback; // a corrupt entry must not break the reader
    }
  }

  async function getJson(url, what) {
    let res;
    try {
      res = await fetch(url);
    } catch {
      throw new DataError("offline", `Couldn't reach ${what}. Check your connection.`);
    }
    if (!res.ok) throw new DataError("not_found", `Couldn't load ${what} (${res.status}).`);
    try {
      return await res.json();
    } catch {
      throw new DataError("invalid", `${what} isn't valid JSON.`);
    }
  }

  async function catalog() {
    if (!catalogPromise) {
      catalogPromise = getJson(catalogUrl, "the course list").then((raw) => ({
        name: raw.name || "Courses",
        lenses: Array.isArray(raw.lenses) ? raw.lenses : [],
        entries: (raw.entries || []).map((e) => ({
          ...e,
          // Relative to catalog.json, not the page: the catalog may live elsewhere.
          bundleUrl: new URL(e.url, catalogUrl).href,
        })),
      }));
      catalogPromise.catch(() => {
        catalogPromise = null; // let a retry fetch again
      });
    }
    return catalogPromise;
  }

  async function course(courseId) {
    if (!bundles.has(courseId)) {
      const p = catalog().then((cat) => {
        const entry = cat.entries.find((e) => e.id === courseId);
        if (!entry) throw new DataError("not_found", "That course isn't in this catalog.");
        return getJson(entry.bundleUrl, `“${entry.title}”`).then((bundle) => {
          bundle.lessons = bundle.lessons || {};
          return bundle;
        });
      });
      p.catch(() => bundles.delete(courseId));
      bundles.set(courseId, p);
    }
    return bundles.get(courseId);
  }

  async function lesson(courseId, lessonId) {
    const bundle = await course(courseId);
    return bundle.lessons[lessonId] || null;
  }

  /** Precomputed only: there is no model behind a static site. */
  async function lens(courseId, lessonId, lensId) {
    const content = await lesson(courseId, lessonId);
    const text = content && content.lenses && content.lenses[lensId];
    return text && text.trim() ? text : null;
  }

  function progress(courseId) {
    return readJson(`progress:${courseId}`, {});
  }

  /**
   * Merge, never replace — the same rule the server's store follows. Progress for
   * a lesson the course no longer has is kept: views ignore ids they can't find,
   * and a later update may bring the lesson back.
   */
  function setProgress(courseId, lessonId, patch) {
    const all = progress(courseId);
    all[lessonId] = { ...(all[lessonId] || {}), ...patch };
    const saved = write(`progress:${courseId}`, JSON.stringify(all));
    return { progress: all, saved };
  }

  /** Courses this reader has opened, most recent first. */
  function library() {
    const ids = readJson("library", []);
    return Array.isArray(ids) ? ids : [];
  }

  function touchLibrary(courseId) {
    const ids = [courseId, ...library().filter((id) => id !== courseId)].slice(0, LIBRARY_MAX);
    write("library", JSON.stringify(ids));
    return ids;
  }

  return { catalog, course, lesson, lens, progress, setProgress, library, touchLibrary };
}
