const backendBaseUrl =
  process.env.BACKEND_BASE_URL ?? "https://124.220.229.9/recognition";

export const dynamic = "force-dynamic";

type RouteContext = {
  params: Promise<{ path?: string[] }>;
};

const textTypes = [
  "text/html",
  "text/css",
  "javascript",
  "application/json",
];

function rewriteText(body: string, contentType: string) {
  let rewritten = body
    .replaceAll("'/api/", "'/system/api/")
    .replaceAll('"/api/', '"/system/api/')
    .replaceAll("`/api/", "`/system/api/");

  if (contentType.includes("text/html")) {
    rewritten = rewritten
      .replaceAll('href="/static/', 'href="/system/static/')
      .replaceAll('src="/static/', 'src="/system/static/')
      .replaceAll("location.href = '/'", "location.href = '/system/'")
      .replaceAll("location.href = '/login'", "location.href = '/system/login'");
  }

  if (contentType.includes("javascript")) {
    rewritten = rewritten
      .replaceAll("'/login'", "'/system/login'")
      .replaceAll('"/login"', '"/system/login"');
  }

  if (contentType.includes("application/json")) {
    rewritten = rewritten
      .replaceAll('"/data/uploads/', '"/system/data/uploads/')
      .replaceAll('"/uploads/', '"/system/uploads/')
      .replaceAll('"/static/', '"/system/static/');
  }

  return rewritten;
}

function rewriteSetCookie(cookie: string) {
  if (/;\s*Path=/i.test(cookie)) {
    return cookie.replace(/;\s*Path=\/[^;]*/i, "; Path=/system");
  }
  return `${cookie}; Path=/system`;
}

async function proxy(request: Request, context: RouteContext) {
  const { path = [] } = await context.params;
  if (path.some((segment) => segment === ".." || segment.includes("\\"))) {
    return new Response("Invalid path", { status: 400 });
  }

  const incomingUrl = new URL(request.url);
  const targetUrl = new URL(
    `${backendBaseUrl.replace(/\/+$/, "")}/${path.map(encodeURIComponent).join("/")}`,
  );
  targetUrl.search = incomingUrl.search;

  const headers = new Headers();
  for (const name of [
    "accept",
    "accept-language",
    "content-type",
    "cookie",
    "user-agent",
  ]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  const upstream = await fetch(targetUrl, {
    method: request.method,
    headers,
    body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
    redirect: "manual",
  });

  const responseHeaders = new Headers();
  for (const name of ["cache-control", "content-disposition", "content-type"]) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }

  const setCookie = upstream.headers.get("set-cookie");
  if (setCookie) responseHeaders.set("set-cookie", rewriteSetCookie(setCookie));

  const location = upstream.headers.get("location");
  if (location) {
    const target = new URL(location, backendBaseUrl);
    responseHeaders.set(
      "location",
      target.origin === new URL(backendBaseUrl).origin
        ? `/system${target.pathname}${target.search}`
        : location,
    );
  }

  const contentType = upstream.headers.get("content-type") ?? "";
  if (textTypes.some((type) => contentType.includes(type))) {
    const body = rewriteText(await upstream.text(), contentType);
    return new Response(body, {
      status: upstream.status,
      headers: responseHeaders,
    });
  }

  return new Response(upstream.body, {
    status: upstream.status,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const HEAD = proxy;
