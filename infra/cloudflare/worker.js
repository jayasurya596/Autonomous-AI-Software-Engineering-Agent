import { DurableObject, WorkerEntrypoint } from "cloudflare:workers";

const CONTAINER_IDLE_TIMEOUT_MS = 5 * 60 * 1000;
const MAX_CONTAINER_READY_WAIT_MS = 4 * 60 * 1000;
const CONTAINER_CONFIGURATION_VERSION = "cloudflare-2026-11-09-llama-3.3-v3";
const CLOUDFLARE_CHAT_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast";
const CLOUDFLARE_EMBEDDING_MODEL = "@cf/baai/bge-base-en-v1.5";
const ALLOWED_AI_MODELS = new Set([CLOUDFLARE_CHAT_MODEL, CLOUDFLARE_EMBEDDING_MODEL]);
const SECURITY_HEADERS = {
  "content-security-policy": "object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
  "permissions-policy": "camera=(), microphone=(), geolocation=()",
  "referrer-policy": "strict-origin-when-cross-origin",
  "x-content-type-options": "nosniff",
  "x-frame-options": "DENY",
};

function secureResponse(response, request) {
  const headers = new Headers(response.headers);
  for (const [name, value] of Object.entries(SECURITY_HEADERS)) {
    headers.set(name, value);
  }
  if (new URL(request.url).protocol === "https:") {
    headers.set("strict-transport-security", "max-age=31536000");
  }
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers,
  });
}

async function handleRepositoryStorage(request, env) {
  const url = new URL(request.url);
  let key;
  try {
    key = decodeURIComponent(url.pathname.slice(1));
  } catch {
    return new Response("Invalid object key.", { status: 400 });
  }
  const segments = key.split("/");
  if (
    !key.startsWith("users/") ||
    segments.some((segment) => !segment || segment === "." || segment === "..")
  ) {
    return new Response("Invalid object key.", { status: 400 });
  }

  if (request.method === "GET") {
    const object = await env.REPOSITORIES.get(key);
    if (!object) return new Response("Object not found.", { status: 404 });
    return new Response(object.body, {
      headers: {
        "content-type": object.httpMetadata?.contentType ?? "application/octet-stream",
        "cache-control": "no-store",
      },
    });
  }
  if (request.method === "PUT") {
    await env.REPOSITORIES.put(key, request.body, {
      httpMetadata: {
        contentType: request.headers.get("content-type") ?? "application/octet-stream",
      },
    });
    return new Response(null, { status: 204 });
  }
  if (request.method === "DELETE") {
    await env.REPOSITORIES.delete(key);
    return new Response(null, { status: 204 });
  }
  if (request.method === "HEAD") {
    const object = await env.REPOSITORIES.head(key);
    return object
      ? new Response(null, { status: 200 })
      : new Response(null, { status: 404 });
  }
  return new Response("Method not allowed.", {
    status: 405,
    headers: { allow: "GET, HEAD, PUT, DELETE" },
  });
}

async function handleWorkersAI(request, env) {
  const url = new URL(request.url);
  if (request.method === "GET" && url.pathname === "/ai/models/schema") {
    const model = url.searchParams.get("model");
    if (!ALLOWED_AI_MODELS.has(model)) {
      return Response.json({ success: false }, { status: 404 });
    }
    return Response.json({ success: true, result: { model } });
  }

  if (request.method !== "POST") {
    return new Response("Method not allowed.", { status: 405 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return new Response("Invalid JSON body.", { status: 400 });
  }

  if (url.pathname === "/ai/v1/chat/completions") {
    if (
      body.model !== CLOUDFLARE_CHAT_MODEL ||
      !Array.isArray(body.messages) ||
      body.stream === true
    ) {
      return new Response("Unsupported model request.", { status: 400 });
    }
    try {
      console.log("workers_ai_chat_started", JSON.stringify({ model: body.model, messageCount: body.messages.length, responseFormatType: body.response_format?.type ?? null }));
      const result = await env.AI.run(body.model, {
        messages: body.messages,
        response_format: body.response_format,
        temperature: body.temperature,
        max_tokens: body.max_tokens,
        stream: false,
      });
      const response = result?.response;
      const content = typeof response === "string" ? response : JSON.stringify(response);
      if (!content) return new Response("The model returned no response.", { status: 502 });
      console.log("workers_ai_chat_succeeded", JSON.stringify({ model: body.model, responseLength: content.length }));
      return Response.json({
        id: `cf-${crypto.randomUUID()}`,
        object: "chat.completion",
        created: Math.floor(Date.now() / 1000),
        model: body.model,
        choices: [
          {
            index: 0,
            message: { role: "assistant", content },
            finish_reason: "stop",
          },
        ],
      });
    } catch (error) {
      console.error("workers_ai_chat_failed", JSON.stringify({ name: error instanceof Error ? error.name : typeof error, status: error?.status ?? null, code: error?.code ?? null }));
      return new Response("Workers AI could not complete the request.", { status: 502 });
    }
  }

  if (url.pathname === "/ai/v1/embeddings") {
    if (body.model !== CLOUDFLARE_EMBEDDING_MODEL) {
      return new Response("Unsupported model request.", { status: 400 });
    }
    const input = typeof body.input === "string" ? [body.input] : body.input;
    if (!Array.isArray(input) || input.length === 0 || !input.every((item) => typeof item === "string")) {
      return new Response("Invalid embedding input.", { status: 400 });
    }
    try {
      const result = await env.AI.run(body.model, { text: input });
      if (!Array.isArray(result?.data) || result.data.length !== input.length) {
        return new Response("Workers AI returned an invalid embedding response.", { status: 502 });
      }
      return Response.json({
        object: "list",
        model: body.model,
        data: result.data.map((embedding, index) => ({
          object: "embedding",
          index,
          embedding,
        })),
      });
    } catch (error) {
      console.error("workers_ai_embedding_failed", JSON.stringify({ name: error instanceof Error ? error.name : typeof error, status: error?.status ?? null, code: error?.code ?? null }));
      return new Response("Workers AI could not create an embedding.", { status: 502 });
    }
  }

  return new Response("Route not found.", { status: 404 });
}

export class ApiContainer extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.starting = undefined;
  }

  async fetch(request) {
    this.starting ??= this.startAndWaitForPort().finally(() => {
      this.starting = undefined;
    });
    await this.starting;

    const url = new URL(request.url);
    url.protocol = "http:";
    url.host = "container";
    const headers = new Headers(request.headers);
    headers.delete("host");
    headers.delete("origin");
    const cloudflareClientIp = request.headers.get("cf-connecting-ip");
    headers.delete("x-agent-client-ip");
    if (cloudflareClientIp) headers.set("x-agent-client-ip", cloudflareClientIp);
    const forwarded = new Request(url, {
      method: request.method,
      headers,
      body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
      redirect: "manual",
    });
    return this.ctx.container.getTcpPort(8000).fetch(forwarded);
  }

  async startAndWaitForPort() {
    const container = this.ctx.container;
    const outboundProxy = this.ctx.exports.ContainerOutboundProxy({ props: {} });
    await Promise.all([
      container.interceptOutboundHttp("repository-storage.internal", outboundProxy),
      container.interceptOutboundHttp("workers-ai.internal", outboundProxy),
    ]);
    const activeConfigurationVersion = await this.ctx.storage.get("container-configuration-version");
    if (container.running && activeConfigurationVersion !== CONTAINER_CONFIGURATION_VERSION) {
      await container.destroy("Container configuration updated");
    }
    if (!container.running) {
      container.start({
        image: container.images.base,
        instance: "standard-1",
        enableInternet: true,
        env: {
          APP_ENV: "production",
          AI_PROVIDER: "cloudflare",
          CLOUDFLARE_MODEL: "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
          CLOUDFLARE_EMBEDDING_MODEL: "@cf/baai/bge-base-en-v1.5",
          FIREBASE_PROJECT_ID: this.env.FIREBASE_PROJECT_ID,
          FIREBASE_ADMIN_CREDENTIALS_JSON: this.env.FIREBASE_ADMIN_CREDENTIALS_JSON,
          DATABASE_URL: this.env.DATABASE_URL,
          GITHUB_CREDENTIALS_ENCRYPTION_KEY: this.env.GITHUB_CREDENTIALS_ENCRYPTION_KEY,
          R2_BUCKET_NAME: this.env.R2_BUCKET_NAME,
          AGENT_REPOSITORIES_ROOT: "/app/repositories",
          SANDBOX_PYTHON_IMAGE: "agent-sandbox-python:cloudflare",
          SANDBOX_NODE_IMAGE: "agent-sandbox-node:cloudflare",
          SANDBOX_TIMEOUT_SECONDS: "120",
          MAX_REQUEST_BODY_BYTES: "25000000",
          MAX_REPOSITORY_UPLOAD_BYTES: "20000000",
          CORS_ORIGINS:
            "https://autonomous-ai-software-engineering-agent.penchalajayasuryachappidi.workers.dev",
        },
      });
    }
    await container.setInactivityTimeout(CONTAINER_IDLE_TIMEOUT_MS);

    const port = container.getTcpPort(8000);
    const deadline = Date.now() + MAX_CONTAINER_READY_WAIT_MS;
    let lastError;
    while (Date.now() < deadline) {
      try {
        const response = await port.fetch("http://container/health", {
          signal: AbortSignal.timeout(2_000),
        });
        await response.body?.cancel();
        if (response.ok) {
          await this.ctx.storage.put(
            "container-configuration-version",
            CONTAINER_CONFIGURATION_VERSION,
          );
          return;
        }
        lastError = new Error(`API health check returned ${response.status}.`);
      } catch (error) {
        lastError = error;
      }
      await scheduler.wait(1_000);
    }
    throw new Error("The API container did not become ready before the startup deadline.", {
      cause: lastError,
    });
  }
}

export class ContainerOutboundProxy extends WorkerEntrypoint {
  fetch(request) {
    const { hostname } = new URL(request.url);
    if (hostname === "repository-storage.internal") {
      return handleRepositoryStorage(request, this.env);
    }
    if (hostname === "workers-ai.internal") {
      return handleWorkersAI(request, this.env);
    }
    return new Response("Outbound host is not configured.", { status: 403 });
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const apiRequest = url.pathname === "/health" || url.pathname.startsWith("/api/");
    if (!apiRequest) return secureResponse(await env.ASSETS.fetch(request), request);

    const origin = request.headers.get("Origin");
    if (origin && origin !== url.origin) {
      return new Response("Cross-origin API requests are not allowed.", { status: 403 });
    }
    return secureResponse(
      await env.API_CONTAINER.getByName("production-api").fetch(request),
      request,
    );
  },
};
