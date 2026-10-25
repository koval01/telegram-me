// Proof of work in the browser (docs/api/protection.md): get a challenge,
// reuse it until shortly before it expires, and find a nonce per request on
// up to four Web Workers.

const CHALLENGE_PATH = "/v1/pow/challenge";
const MARGIN_MS = 15_000;
const assets = new URL(import.meta.url).search;
let current = null;

/** Keep a challenge a rejection carried, so the next request uses it. */
export function remember(pow) {
  if (pow && pow.challenge) current = pow;
}

async function challenge() {
  if (current && current.expires * 1000 - Date.now() > MARGIN_MS) return current;
  const response = await fetch(CHALLENGE_PATH, { cache: "no-store" });
  if (!response.ok) throw new Error(`challenge request failed: ${response.status}`);
  current = await response.json();
  return current;
}

function search(pow, method, target) {
  const count = Math.max(1, Math.min(navigator.hardwareConcurrency || 2, 4));
  const workers = Array.from(
    { length: count },
    () => new Worker(new URL(`pow-worker.js${assets}`, import.meta.url)),
  );
  const stop = () => workers.forEach((worker) => worker.terminate());
  return new Promise((resolve, reject) => {
    workers.forEach((worker, index) => {
      worker.onmessage = ({ data }) => {
        stop();
        if (typeof data?.nonce === "string") resolve(data.nonce);
        else reject(new Error(data?.error || "proof-of-work worker failed"));
      };
      worker.onerror = (event) => {
        stop();
        reject(new Error(event.message || "proof-of-work worker failed"));
      };
      worker.postMessage({
        challenge: pow.challenge,
        difficulty: pow.difficulty,
        method,
        target,
        start: index,
        step: count,
      });
    });
  });
}

/** The `X-PoW` header value for one request: `challenge:nonce`. */
export async function authorize(method, target) {
  const pow = await challenge();
  const nonce = await search(pow, method.toUpperCase(), target);
  return `${pow.challenge}:${nonce}`;
}

/** Same-origin `fetch` that answers proof of work, retrying once with a fresh challenge. */
export async function powFetch(path, init = {}) {
  const method = (init.method || "GET").toUpperCase();
  const url = new URL(path, location.href);
  const target = url.pathname + url.search;
  const send = async () => {
    const headers = new Headers(init.headers);
    headers.set("X-PoW", await authorize(method, target));
    return fetch(url, { ...init, method, headers });
  };
  let response = await send();
  if (response.status === 428 || response.status === 403) {
    const body = await response.clone().json().catch(() => null);
    if (body && body.pow) {
      remember(body.pow);
      response = await send();
    }
  }
  return response;
}
