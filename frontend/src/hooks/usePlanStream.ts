import { useCallback, useRef, useState } from 'react';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api';

export type PlanStreamEvent =
  | { type: 'node'; data: Record<string, unknown> }
  | { type: 'clarify'; missing: string[]; data: Record<string, unknown> }
  | { type: 'final'; data: Record<string, unknown> }
  | { type: 'error'; error: string };

export function usePlanStream() {
  const controllerRef = useRef<AbortController | null>(null);
  const [streaming, setStreaming] = useState(false);

  const start = useCallback(
    async (
      payload: Record<string, unknown>,
      onEvent: (event: PlanStreamEvent) => void,
    ) => {
      controllerRef.current?.abort();
      const controller = new AbortController();
      controllerRef.current = controller;
      setStreaming(true);

      try {
        const response = await fetch(`${API_BASE_URL}/plan/stream`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
          signal: controller.signal,
        });

        if (!response.ok) {
          const text = await response.text();
          onEvent({ type: 'error', error: text });
          return;
        }

        const reader = response.body?.getReader();
        if (!reader) {
          onEvent({ type: 'error', error: '浏览器不支持流式响应' });
          return;
        }

        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          let boundary = buffer.indexOf('\n\n');
          while (boundary >= 0) {
            const raw = buffer.slice(0, boundary).trim();
            buffer = buffer.slice(boundary + 2);
            boundary = buffer.indexOf('\n\n');

            if (!raw.startsWith('data: ')) continue;
            const dataText = raw.slice(6).trim();
            if (dataText === '[DONE]') continue;
            try {
              const parsed = JSON.parse(dataText) as {
                type?: string;
                data?: Record<string, unknown>;
                error?: string;
                missing?: unknown;
              };
              if (parsed.type === 'final') {
                onEvent({ type: 'final', data: parsed.data ?? {} });
              } else if (parsed.type === 'clarify') {
                onEvent({
                  type: 'clarify',
                  missing: Array.isArray(parsed.missing) ? parsed.missing : [],
                  data: parsed,
                });
              } else if (parsed.type === 'error') {
                onEvent({ type: 'error', error: parsed.error ?? '未知错误' });
              } else {
                onEvent({ type: 'node', data: parsed });
              }
            } catch {
              // 忽略无法解析的行
            }
          }
        }
      } catch (error) {
        if ((error as Error).name !== 'AbortError') {
          onEvent({ type: 'error', error: (error as Error).message });
        }
      } finally {
        setStreaming(false);
      }
    },
    [],
  );

  const stop = useCallback(() => {
    controllerRef.current?.abort();
  }, []);

  return { start, stop, streaming };
}
