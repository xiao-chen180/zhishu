// 共享 SSE 流式事件处理工具
// 统一 retry / timeout / abort 逻辑，减少 4 处重复实现

export interface ChatEvent {
  type: 'session' | 'progress' | 'result' | 'done' | 'error' | 'token' | 'thinking' | 'analysis' | 'similar'
  session_id?: string
  progress?: number
  message?: string
  content?: string
  data?: any
  step?: number
  text?: string
}

const MAX_RETRIES = 2
const INITIAL_DELAY = 1000

export interface EventStreamOptions {
  /** 单次连接超时（毫秒），默认 120s */
  timeoutMs?: number
  /**
   * 连接失败是否自动重试，默认 true。
   * 生成/写入类 POST 接口应传 false——重试会重复生成资源、重复写入消息。
   */
  retry?: boolean
}

export async function fetchSSEStream(
  url: string,
  body: object,
  onEvent: (e: ChatEvent) => void,
  signal: AbortSignal,
): Promise<void> {
  const token = typeof window !== 'undefined' ? localStorage.getItem('zhishu_token') : null
  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok || !res.body) throw new Error(`SSE ${res.status}`)
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try { onEvent(JSON.parse(line.slice(6))) } catch { /* ignore malformed */ }
      }
    }
  }
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

export function createEventStream(
  url: string,
  body: object,
  onEvent: (e: ChatEvent) => void,
  options: number | EventStreamOptions = {},
): () => void {
  const { timeoutMs = 120_000, retry = true } =
    typeof options === 'number' ? { timeoutMs: options } : options

  const controller = new AbortController()
  let stopped = false
  let retries = 0
  let timedOut = false

  async function run() {
    while (!stopped) {
      timedOut = false
      const timeout = setTimeout(() => {
        if (!stopped) {
          timedOut = true
          controller.abort()
        }
      }, timeoutMs)
      try {
        await fetchSSEStream(url, body, onEvent, controller.signal)
        return // success
      } catch (err: any) {
        // 用户主动取消：静默退出
        if (stopped) return

        // 超时：必须通知调用方，否则 UI 会永久停在"生成中"
        if (timedOut || err?.name === 'AbortError') {
          onEvent({
            type: 'error',
            message: `请求超时（${Math.round(timeoutMs / 1000)} 秒），请稍后重试`,
          })
          return
        }

        // 网络/HTTP 失败
        if (!retry) {
          onEvent({ type: 'error', message: '连接失败，请稍后重试' })
          return
        }
        retries++
        if (retries > MAX_RETRIES) {
          onEvent({ type: 'error', message: `连接失败（已重试 ${MAX_RETRIES} 次）` })
          return
        }
        await delay(INITIAL_DELAY * retries)
      } finally {
        clearTimeout(timeout)
      }
    }
  }

  run()
  return () => { stopped = true; controller.abort() }
}
