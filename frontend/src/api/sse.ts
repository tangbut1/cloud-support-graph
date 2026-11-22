/**
 * SSE 流式解析器：通过 ReadableStream 解析后端 POST /api/conversations/{id}/chat/stream 返回的 text/event-stream。
 */

import { ApiError, clearAuthStorage, getToken } from "./client";

export type SseEventHandler = (event: string, data: any) => void;

export async function streamChat(
  conversationId: string,
  message: string,
  imageFile: File | null,
  onEvent: SseEventHandler,
  signal?: AbortSignal
): Promise<void> {
  const form = new FormData();
  form.append("message", message);
  if (imageFile) {
    form.append("image", imageFile);
  }

  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const res = await fetch(`/api/conversations/${conversationId}/chat/stream`, {
    method: "POST",
    headers,
    body: form,
    signal,
  });

  if (res.status === 401) {
    clearAuthStorage();
    window.location.href = "/login";
    throw new ApiError(401, "登录已过期，请重新登录");
  }

  if (!res.ok) {
    let detail = `发送失败 (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // 忽略
    }
    throw new ApiError(res.status, detail);
  }

  if (!res.body) {
    throw new Error("浏览器不支持流式读取响应体");
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // 兼容 CRLF 与 LF
    const normalized = buffer.replace(/\r\n/g, "\n");
    const chunks = normalized.split("\n\n");
    buffer = chunks.pop() ?? "";
    for (const chunk of chunks) {
      if (!chunk.trim()) continue;
      const lines = chunk.split("\n");
      const evLine = lines.find((l) => l.startsWith("event:"));
      const dataLine = lines.find((l) => l.startsWith("data:"));
      const ev = evLine?.slice(6).trim();
      const rawData = dataLine?.slice(5).trim();
      if (ev && rawData) {
        try {
          onEvent(ev, JSON.parse(rawData));
        } catch {
          onEvent(ev, { raw: rawData });
        }
      }
    }
  }
}
