// The reader's data layer, under `node --test`: no browser, no dependencies.
// Progress now lives client-side, so the rules the server's store used to pin
// (merge, don't replace; ids are stable) are pinned here.

import assert from "node:assert/strict";
import { test } from "node:test";

import { createData } from "../../src/syllabus_studio/web/static/js/data.js";

function memoryStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
    dump: () => Object.fromEntries(m),
  };
}

function throwingStorage() {
  const boom = () => {
    throw new Error("QuotaExceededError");
  };
  return { getItem: boom, setItem: boom, removeItem: boom };
}

/** A fake fetch serving a URL -> JSON map, recording what was asked for. */
function fakeFetch(files) {
  const calls = [];
  const fn = async (url) => {
    calls.push(String(url));
    if (!(url in files)) return { ok: false, status: 404, json: async () => ({}) };
    return { ok: true, status: 200, json: async () => structuredClone(files[url]) };
  };
  fn.calls = calls;
  return fn;
}

const SITE = "https://example.github.io/syllabus-studio/";

function siteFiles() {
  return {
    [`${SITE}catalog.json`]: {
      name: "Courses",
      lenses: [{ id: "eli5", label: "Explain simply" }],
      entries: [{ id: "ml", title: "Applied ML", url: "courses/ml.course.json", lessonCount: 2 }],
    },
    [`${SITE}courses/ml.course.json`]: {
      course: { id: "ml", title: "Applied ML", modules: [] },
      lessons: { m1l1: { bigIdea: "x", lenses: { eli5: "Simply put…", rigor: "   " } } },
    },
  };
}

test("progress merges rather than replaces", () => {
  const data = createData({ catalogUrl: `${SITE}catalog.json`, storage: memoryStorage() });

  data.setProgress("ml", "m1l1", { score: 3, total: 5 });
  const { progress } = data.setProgress("ml", "m1l1", { done: true });

  assert.deepEqual(progress.m1l1, { score: 3, total: 5, done: true });
  assert.deepEqual(data.progress("ml").m1l1, { score: 3, total: 5, done: true });
});

test("progress survives a new page load, because it is in storage", () => {
  const storage = memoryStorage();
  createData({ catalogUrl: `${SITE}catalog.json`, storage }).setProgress("ml", "m1l1", { done: true });

  const later = createData({ catalogUrl: `${SITE}catalog.json`, storage });
  assert.equal(later.progress("ml").m1l1.done, true);
});

test("progress for a lesson the course no longer has is kept, not deleted", () => {
  const data = createData({ catalogUrl: `${SITE}catalog.json`, storage: memoryStorage() });
  data.setProgress("ml", "m9l9", { done: true }); // an id a later version dropped
  data.setProgress("ml", "m1l1", { done: true });

  assert.deepEqual(Object.keys(data.progress("ml")).sort(), ["m1l1", "m9l9"]);
});

test("progress is per course", () => {
  const data = createData({ catalogUrl: `${SITE}catalog.json`, storage: memoryStorage() });
  data.setProgress("ml", "m1l1", { done: true });
  assert.deepEqual(data.progress("stats"), {});
});

test("storage that throws degrades to memory and warns exactly once", () => {
  let warnings = 0;
  const data = createData({
    catalogUrl: `${SITE}catalog.json`,
    storage: throwingStorage(),
    onStorageError: () => warnings++,
  });

  assert.deepEqual(data.progress("ml"), {});
  const first = data.setProgress("ml", "m1l1", { done: true });
  data.setProgress("ml", "m1l2", { done: true });

  assert.equal(first.saved, false);
  assert.equal(data.progress("ml").m1l1.done, true, "the page keeps working for this visit");
  assert.equal(warnings, 1);
});

test("no storage at all behaves like failing storage", () => {
  let warnings = 0;
  const data = createData({ catalogUrl: `${SITE}catalog.json`, storage: null, onStorageError: () => warnings++ });
  data.setProgress("ml", "m1l1", { done: true });
  assert.equal(data.progress("ml").m1l1.done, true);
  assert.equal(warnings, 1);
});

test("a corrupt progress entry reads as empty instead of breaking the reader", () => {
  const storage = memoryStorage();
  storage.setItem("ss:v1:progress:ml", "{not json");
  const data = createData({ catalogUrl: `${SITE}catalog.json`, storage });
  assert.deepEqual(data.progress("ml"), {});
});

test("bundle urls resolve relative to catalog.json on a sub-path host", async () => {
  const fetch = fakeFetch(siteFiles());
  const data = createData({ catalogUrl: `${SITE}catalog.json`, fetch, storage: memoryStorage() });

  const catalog = await data.catalog();
  assert.equal(catalog.entries[0].bundleUrl, `${SITE}courses/ml.course.json`);

  const bundle = await data.course("ml");
  assert.equal(bundle.course.title, "Applied ML");
  assert.ok(fetch.calls.includes(`${SITE}courses/ml.course.json`));
});

test("a course bundle is fetched once per page, however often it is asked for", async () => {
  const fetch = fakeFetch(siteFiles());
  const data = createData({ catalogUrl: `${SITE}catalog.json`, fetch, storage: memoryStorage() });

  await Promise.all([data.course("ml"), data.course("ml"), data.lesson("ml", "m1l1")]);
  assert.equal(fetch.calls.filter((u) => u.endsWith("ml.course.json")).length, 1);
});

test("lenses are precomputed only: missing or blank means none", async () => {
  const data = createData({ catalogUrl: `${SITE}catalog.json`, fetch: fakeFetch(siteFiles()), storage: memoryStorage() });

  assert.equal(await data.lens("ml", "m1l1", "eli5"), "Simply put…");
  assert.equal(await data.lens("ml", "m1l1", "rigor"), null, "whitespace is not a lens");
  assert.equal(await data.lens("ml", "m1l1", "analogy"), null);
  assert.equal(await data.lesson("ml", "m2l1"), null);
});

test("a course that isn't in the catalog is a not_found error", async () => {
  const data = createData({ catalogUrl: `${SITE}catalog.json`, fetch: fakeFetch(siteFiles()), storage: memoryStorage() });
  await assert.rejects(data.course("nope"), { code: "not_found" });
});

test("a failed catalog fetch can be retried", async () => {
  const files = {};
  const fetch = fakeFetch(files);
  const data = createData({ catalogUrl: `${SITE}catalog.json`, fetch, storage: memoryStorage() });

  await assert.rejects(data.catalog(), { code: "not_found" });
  Object.assign(files, siteFiles()); // the site finished deploying
  assert.equal((await data.catalog()).entries.length, 1);
});

test("the library lists opened courses, most recent first, without duplicates", () => {
  const data = createData({ catalogUrl: `${SITE}catalog.json`, storage: memoryStorage() });
  data.touchLibrary("ml");
  data.touchLibrary("stats");
  data.touchLibrary("ml");
  assert.deepEqual(data.library(), ["ml", "stats"]);
});
