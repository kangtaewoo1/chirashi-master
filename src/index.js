/**
 * Chirashi Master v6 - Cloudflare Worker 프록시
 * Flask 백엔드를 Cloudflare로 연결하는 미니 프록시 서버
 */

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    
    // 정적 파일은 Cloudflare에서 캐싱
    if (url.pathname.match(/\.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2)$/)) {
      return cacheStaticAsset(request, env);
    }

    // Flask 백엔드로 프록시 (로컬 또는 배포된 서버)
    const backendUrl = env.BACKEND_URL || 'http://localhost:8888';
    
    try {
      const response = await fetch(backendUrl + url.pathname + url.search, {
        method: request.method,
        headers: request.headers,
        body: request.body,
      });

      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: response.headers,
      });
    } catch (error) {
      return new Response(
        JSON.stringify({ error: 'Backend connection failed', message: error.message }),
        { status: 503, headers: { 'Content-Type': 'application/json' } }
      );
    }
  },
};

async function cacheStaticAsset(request, env) {
  const cache = caches.default;
  const cachedResponse = await cache.match(request);
  
  if (cachedResponse) return cachedResponse;

  const backendUrl = env.BACKEND_URL || 'http://localhost:8888';
  const url = new URL(request.url);
  
  const response = await fetch(backendUrl + url.pathname + url.search, {
    method: request.method,
    headers: request.headers,
  });

  if (response.ok) {
    ctx.waitUntil(cache.put(request, response.clone()));
  }

  return response;
}
