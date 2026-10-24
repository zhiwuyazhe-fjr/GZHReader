export interface CoreProcess {
  write(data: string): Promise<void>
  kill(): Promise<void>
}

export interface ProcessEvents {
  data(chunk: string): void
  exit(error: Error): void
}

type Listener = (payload: unknown) => void | Promise<void>
type Pending = {
  resolve(value: unknown): void
  reject(reason: Error): void
  timer: ReturnType<typeof setTimeout>
}

export interface RpcOptions {
  spawn(events: ProcessEvents): Promise<CoreProcess>
  timeoutMs?: number
  restartDelayMs?: number
  maxRestarts?: number
  stableMs?: number
}

/** Owns one process generation. Requests are never replayed after a crash. */
export class RpcClient {
  private child: CoreProcess | null = null
  private starting: Promise<void> | null = null
  private restartTimer: ReturnType<typeof setTimeout> | null = null
  private pending = new Map<number, Pending>()
  private listeners = new Map<string, Set<Listener>>()
  private buffer = ''
  private id = 0
  private generation = 0
  private restarts = 0
  private startedAt = 0
  private stopped = false
  private readonly options: RpcOptions

  constructor(options: RpcOptions) { this.options = options }

  start(): Promise<void> {
    if (this.stopped) return Promise.reject(new Error('本地核心已经停止'))
    if (this.child) return Promise.resolve()
    if (this.starting) return this.starting
    this.cancelRestart()
    const generation = ++this.generation
    const attempt = Promise.resolve().then(async () => {
      const child = await this.options.spawn({
        data: chunk => { if (generation === this.generation) this.receive(chunk) },
        exit: error => this.handleExit(generation, error),
      })
      if (this.stopped || generation !== this.generation) {
        await child.kill().catch(() => undefined)
        throw new Error('本地核心已经停止')
      }
      this.child = child
      this.startedAt = Date.now()
      this.emit('core.connected', {})
    }).catch(error => {
      this.handleExit(generation, asError(error))
      throw error
    }).finally(() => {
      if (this.starting === attempt) this.starting = null
    })
    this.starting = attempt
    return attempt
  }

  on<T = unknown>(event: string, listener: (payload: T) => void | Promise<void>): () => void {
    const listeners = this.listeners.get(event) ?? new Set<Listener>()
    const callback: Listener = payload => listener(payload as T)
    listeners.add(callback)
    this.listeners.set(event, listeners)
    return () => {
      listeners.delete(callback)
      if (!listeners.size) this.listeners.delete(event)
    }
  }

  private emit(event: string, payload: unknown) {
    for (const listener of this.listeners.get(event) ?? []) {
      try {
        Promise.resolve(listener(payload)).catch(error => console.warn('Core event handler failed', error))
      } catch (error) {
        console.warn('Core event handler failed', error)
      }
    }
  }

  private receive(chunk: string) {
    this.buffer += chunk
    const lines = this.buffer.split(/\r?\n/)
    this.buffer = lines.pop() ?? ''
    for (const line of lines) {
      if (!line.trim()) continue
      try { this.parse(JSON.parse(line)) }
      catch { console.warn('Invalid core message') }
    }
  }

  private parse(message: unknown) {
    if (!message || typeof message !== 'object') return
    const payload = message as { event?: string; payload?: unknown; id?: number; error?: { message?: string }; result?: unknown }
    if (payload.event) this.emit(payload.event, payload.payload)
    if (payload.id == null) return
    const pending = this.pending.get(payload.id)
    if (!pending) return
    clearTimeout(pending.timer)
    this.pending.delete(payload.id)
    if (payload.error) pending.reject(new Error(payload.error.message || '操作没有完成'))
    else pending.resolve(payload.result)
  }

  private handleExit(generation: number, error: Error) {
    if (generation !== this.generation) return
    ++this.generation // Ignore duplicate close/error events and late output from this child.
    const child = this.child
    this.child = null
    this.buffer = ''
    this.rejectPending(error)
    void child?.kill().catch(() => undefined)
    this.emit('core.disconnected', {})
    if (this.stopped) return
    if (this.startedAt && Date.now() - this.startedAt >= (this.options.stableMs ?? 30_000)) this.restarts = 0
    this.startedAt = 0
    if (this.restarts >= (this.options.maxRestarts ?? 3)) {
      this.emit('core.error', { message: '本地核心多次停止，请重试或重新打开应用' })
      return
    }
    ++this.restarts
    this.emit('core.error', { message: '本地核心正在恢复连接' })
    this.restartTimer = setTimeout(() => {
      this.restartTimer = null
      void this.start().catch(() => undefined)
    }, (this.options.restartDelayMs ?? 800) * this.restarts)
  }

  private request<T>(child: CoreProcess, method: string, params: Record<string, unknown>, timeoutMs: number): Promise<T> {
    const id = ++this.id
    return new Promise<T>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id)
        reject(new Error('操作等待时间较长，请稍后查看结果'))
      }, timeoutMs)
      this.pending.set(id, { resolve: value => resolve(value as T), reject, timer })
      // Attach the rejection handler before returning; a broken pipe must settle this request.
      void Promise.resolve().then(() => child.write(JSON.stringify({ jsonrpc: '2.0', id, method, params }) + '\n'))
        .catch(error => {
          const pending = this.pending.get(id)
          if (!pending) return
          clearTimeout(pending.timer)
          this.pending.delete(id)
          pending.reject(asError(error))
          if (child === this.child) this.handleExit(this.generation, asError(error))
        })
    })
  }

  async call<T = unknown>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    await this.start()
    if (!this.child) throw new Error('本地核心暂时无法启动')
    const timeout = this.options.timeoutMs ?? (method === 'subscriptions.resolve_link' ? 180_000 : 120_000)
    return this.request<T>(this.child, method, params, timeout)
  }

  async stop() {
    this.stopped = true
    this.cancelRestart()
    this.rejectPending(new Error('本地核心已经停止'))
    const child = this.child
    try {
      if (child) await this.request(child, 'core.shutdown', {}, 2_000)
    } catch { /* A non-responsive process is killed below. */ }
    finally {
      ++this.generation
      this.child = null
      this.buffer = ''
      this.rejectPending(new Error('本地核心已经停止'))
      await child?.kill().catch(() => undefined)
      await this.starting?.catch(() => undefined)
    }
  }

  private cancelRestart() {
    if (this.restartTimer) clearTimeout(this.restartTimer)
    this.restartTimer = null
  }

  private rejectPending(error: Error) {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer)
      pending.reject(error)
    }
    this.pending.clear()
  }
}

function asError(error: unknown): Error {
  return error instanceof Error ? error : new Error(String(error))
}
