/**
 * Every call to the server lives here.
 *
 * This is the seam: the rest of the frontend knows nothing about HTTP, so a
 * React or HTMX rewrite reuses this file unchanged, and a different backend
 * only has to match these paths.
 */

const BASE = "/api/v1";

export class ApiError extends Error {
  constructor(code, message, status) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

async function req(method, path, body) {
  let res;
  try {
    res = await fetch(BASE + path, {
      method,
      headers: body === undefined ? undefined : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    throw new ApiError("offline", "The server is not responding. Is it still running?", 0);
  }

  if (res.status === 204) return null;

  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    /* a non-JSON error body; fall through */
  }

  if (!res.ok) {
    throw new ApiError(
      (data && data.code) || "upstream_error",
      (data && data.message) || res.statusText || "Request failed",
      res.status,
    );
  }
  return data;
}

const enc = encodeURIComponent;

export const api = {
  health: () => req("GET", "/health"),
  lenses: () => req("GET", "/lenses"),

  sampleSyllabus: async () => {
    const res = await fetch(`${BASE}/sample-syllabus`);
    return res.ok ? res.text() : "";
  },

  listCourses: () => req("GET", "/courses"),
  getCourse: (id) => req("GET", `/courses/${enc(id)}`),
  createCourse: (payload) => req("POST", "/courses", payload),
  deleteCourse: (id) => req("DELETE", `/courses/${enc(id)}`),

  builtLessons: (id) => req("GET", `/courses/${enc(id)}/lessons`),
  getLesson: (cid, lid) => req("GET", `/courses/${enc(cid)}/lessons/${enc(lid)}`),
  generateLesson: (cid, lid, force = false) =>
    req("POST", `/courses/${enc(cid)}/lessons/${enc(lid)}/generate?force=${force}`),
  setProgress: (cid, lid, patch) =>
    req("PUT", `/courses/${enc(cid)}/lessons/${enc(lid)}/progress`, patch),

  catalog: () => req("GET", "/catalog"),
  installEntry: (entryId) => req("POST", `/catalog/${enc(entryId)}/install`),
  importBundle: (bundle) => req("POST", "/courses/import", { bundle }),
  exportUrl: (cid) => `${BASE}/courses/${enc(cid)}/export`,
};

/**
 * Read one server-sent-event stream.
 *
 * Calls `onDelta(wholeSoFar, delta)` as text arrives and resolves with the
 * complete answer. Rejects with an ApiError carrying `.partial` when the
 * server reports a mid-stream failure.
 */
export async function streamSSE(path, body, { onDelta, signal } = {}) {
  let res;
  try {
    res = await fetch(BASE + path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (e) {
    if (e && e.name === "AbortError") throw new ApiError("cancelled", "Stopped.", 0);
    throw new ApiError("offline", "The server is not responding.", 0);
  }

  if (!res.ok || !res.body) {
    let data = null;
    try {
      data = await res.json();
    } catch {
      /* ignore */
    }
    throw new ApiError(
      (data && data.code) || "upstream_error",
      (data && data.message) || "The stream could not be opened.",
      res.status,
    );
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let whole = "";

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let split;
      while ((split = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);

        let event = "message";
        let data = "";
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        }
        if (!data) continue;

        let payload;
        try {
          payload = JSON.parse(data);
        } catch {
          continue;
        }

        if (event === "delta") {
          whole += payload.text;
          if (onDelta) onDelta(whole, payload.text);
        } else if (event === "done") {
          return typeof payload.text === "string" ? payload.text : whole;
        } else if (event === "error") {
          const err = new ApiError(payload.code || "upstream_error", payload.message || "Failed", 200);
          err.partial = payload.partial || whole;
          throw err;
        }
      }
    }
  } catch (e) {
    if (signal && signal.aborted) {
      const err = new ApiError("cancelled", "Stopped.", 0);
      err.partial = whole;
      throw err;
    }
    throw e;
  }

  return whole;
}

/** Server error code -> something worth showing a person. */
export function errCopy(e) {
  const c = (e && e.code) || "";
  if (c === "cancelled") return "";
  if (c === "offline") return "The server isn't responding. Check the terminal running it.";
  if (c === "not_configured")
    return "No model is configured. Set ANTHROPIC_API_KEY and SS_LLM_PROVIDER in .env, then restart.";
  if (c === "rate_limited") return "Rate limited. Wait a moment and try again — nothing was lost.";
  if (c === "prompt_too_large") return "That syllabus is too long. Trim it to the description, outcomes, and schedule.";
  if (c === "too_short") return "Paste more — at least the course description and the topic list.";
  if (c === "refused") return "The model declined to work with that text. Try a different syllabus.";
  if (c === "invalid_json") return "The model's answer came back malformed. Try again — it usually works on the second pass.";
  if (c === "empty_completion") return "The model returned nothing. Try again with a bit more detail.";
  if (c === "not_found") return "That isn't here any more. Reload the page.";
  return (e && e.message) || "Something went wrong. Try again in a moment.";
}
