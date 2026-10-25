// API reference (Swagger UI at /api): a link back to the landing page and,
// when the server requires proof of work, a request interceptor that solves
// it for every call made with "Try it out". Loaded between the Swagger UI
// bundle and the inline `SwaggerUIBundle({...})` call, which it wraps.
(() => {
  const pow = document.body.dataset.pow === "on";
  const bundle = window.SwaggerUIBundle;
  const assets = new URL(document.currentScript.src).search;

  const bar = document.createElement("nav");
  bar.setAttribute("aria-label", "Site");
  bar.style.cssText =
    "display:flex;gap:18px;align-items:center;padding:10px 20px;font:600 14px/1.4 system-ui,sans-serif;" +
    "border-bottom:1px solid #e3e6ee;background:#fafbfd";
  const links = [
    ["← TelegramMe", "/"],
    ["Docs", "/docs/"],
  ];
  if (document.body.dataset.graphql === "on") links.push(["GraphQL", "/graphql"]);
  for (const [label, href] of links) {
    const link = document.createElement("a");
    link.href = href;
    link.textContent = label;
    link.style.cssText = "color:#3b3fc4;text-decoration:none";
    bar.append(link);
  }
  document.body.prepend(bar);

  if (!pow || typeof bundle !== "function") return;

  const needsProof = (url) =>
    url.origin === location.origin &&
    (url.pathname === "/graphql" || (url.pathname.startsWith("/v1/") && url.pathname !== "/v1/pow/challenge"));

  const solver = import(`/static/pow.js${assets}`);

  async function requestInterceptor(request) {
    const url = new URL(request.url, location.href);
    if (!needsProof(url)) return request;
    const { authorize } = await solver;
    request.headers["X-PoW"] = await authorize(request.method || "GET", url.pathname + url.search);
    return request;
  }

  async function responseInterceptor(response) {
    const body = response.body || response.obj;
    if ((response.status === 428 || response.status === 403) && body && body.pow) {
      (await solver).remember(body.pow);
    }
    return response;
  }

  const wrapped = (options) => bundle({ ...options, requestInterceptor, responseInterceptor });
  window.SwaggerUIBundle = Object.assign(wrapped, bundle);
})();
