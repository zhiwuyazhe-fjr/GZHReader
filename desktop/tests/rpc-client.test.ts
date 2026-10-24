import assert from 'node:assert/strict'
import { test } from 'node:test'
import { setTimeout as delay } from 'node:timers/promises'
import { RpcClient, type ProcessEvents, type CoreProcess } from '../src/lib/rpc-client.ts'
import { dayKey } from '../src/lib/dates.ts'

function fixture(options: { failWrite?: boolean; failSpawn?: boolean; immediateExit?: boolean; maxRestarts?: number; timeoutMs?: number } = {}) {
  const processes: Array<{ events: ProcessEvents; child: CoreProcess; killed: number; writes: Array<{ id: number; method: string }> }> = []
  let spawns = 0
  const client = new RpcClient({
    timeoutMs: options.timeoutMs ?? 1_000, restartDelayMs: 5, maxRestarts: options.maxRestarts ?? 0,
    async spawn(events) {
      ++spawns
      if (options.failSpawn) throw new Error('spawn failed')
      const process = {
        events, killed: 0, writes: [] as Array<{ id: number; method: string }>,
        child: {
          async write(data: string) {
            if (options.failWrite) throw new Error('broken pipe')
            const request = JSON.parse(data)
            process.writes.push(request)
            if (request.method === 'core.shutdown') events.data(`${JSON.stringify({ id: request.id, result: { ok: true } })}\n`)
          },
          async kill() { ++process.killed },
        },
      }
      processes.push(process)
      if (options.immediateExit) events.exit(new Error('exited during spawn'))
      return process.child
    },
  })
  return { client, processes, spawns: () => spawns }
}

test('coalesces concurrent starts and routes out-of-order, fragmented JSON replies', async () => {
  const { client, processes, spawns } = fixture()
  const first = client.call<string>('first')
  const second = client.call<string>('second')
  await delay(0)
  assert.equal(spawns(), 1)
  const process = processes[0]!
  const [a, b] = process.writes
  process.events.data(`${JSON.stringify({ id: b!.id, result: 'second' })}\n{"id":${a!.id},"res`)
  process.events.data('ult":"中文"}\n')
  assert.deepEqual(await Promise.all([first, second]), ['中文', 'second'])
  await client.stop()
})

test('a broken stdin rejects immediately and leaves no later timeout rejection', async () => {
  const { client, processes } = fixture({ failWrite: true })
  await assert.rejects(client.call('write'), /broken pipe/)
  // A later shutdown must still complete rather than waiting for the broken request.
  await client.stop()
  assert.ok(processes[0]!.killed > 0)
})

test('failed spawn does not leave a rejected start promise cached', async () => {
  const { client, spawns } = fixture({ failSpawn: true })
  await assert.rejects(client.start(), /spawn failed/)
  await assert.rejects(client.start(), /spawn failed/)
  assert.equal(spawns(), 2)
  await client.stop()
})

test('a crash rejects every in-flight request and ignores duplicate exit events', async () => {
  const { client, processes } = fixture()
  const first = client.call('first')
  const second = client.call('second')
  const rejected = Promise.all([assert.rejects(first, /crash/), assert.rejects(second, /crash/)])
  await delay(0)
  processes[0]!.events.exit(new Error('crash'))
  processes[0]!.events.exit(new Error('duplicate'))
  await rejected
  await client.stop()
})

test('automatic recovery is bounded even when each restarted process spawns successfully', async () => {
  const { client, processes, spawns } = fixture({ maxRestarts: 2 })
  await client.start()
  processes[0]!.events.exit(new Error('crash'))
  await delay(20)
  assert.equal(spawns(), 2)
  processes[1]!.events.exit(new Error('crash'))
  await delay(30)
  assert.equal(spawns(), 3)
  processes[2]!.events.exit(new Error('crash'))
  await delay(30)
  assert.equal(spawns(), 3)
  await client.stop()
})

test('stop cancels scheduled recovery and prevents later calls from respawning', async () => {
  const { client, processes, spawns } = fixture({ maxRestarts: 2 })
  await client.start()
  processes[0]!.events.exit(new Error('crash'))
  await client.stop()
  await delay(30)
  assert.equal(spawns(), 1)
  await assert.rejects(client.call('after-stop'), /已经停止/)
})

test('a process that exits during spawn is never published as a live child', async () => {
  const { client, processes } = fixture({ immediateExit: true })
  await assert.rejects(client.start(), /已经停止/)
  assert.equal(processes[0]!.killed, 1)
  await client.stop()
})

test('late output from an old process cannot resolve requests in a new generation', async () => {
  const { client, processes } = fixture()
  await client.start()
  const old = processes[0]!
  old.events.exit(new Error('crash'))
  const next = client.call<string>('next')
  await delay(0)
  const current = processes[1]!
  const id = current.writes[0]!.id
  old.events.data(`${JSON.stringify({ id, result: 'stale' })}\n`)
  current.events.data(`${JSON.stringify({ id, result: 'current' })}\n`)
  assert.equal(await next, 'current')
  await client.stop()
})

test('listener failures and malformed lines do not discard a valid RPC response', async () => {
  const { client, processes } = fixture()
  let received = 0
  const originalWarn = console.warn
  console.warn = () => {}
  try {
    client.on('event', () => { throw new Error('bad listener') })
    client.on('event', () => { received++ })
    const request = client.call('request')
    await delay(0)
    const process = processes[0]!
    const id = process.writes[0]!.id
    process.events.data(`not-json\nnull\n{"event":"event","payload":{}}\n${JSON.stringify({ id, result: true })}\n`)
    assert.equal(await request, true)
    assert.equal(received, 1)
    await client.stop()
  } finally { console.warn = originalWarn }
})

test('a timed-out request does not consume a later response', async () => {
  const { client, processes } = fixture({ timeoutMs: 10 })
  await assert.rejects(client.call('timeout'), /等待时间/)
  const process = processes[0]!
  process.events.data(`${JSON.stringify({ id: process.writes[0]!.id, result: 'late' })}\n`)
  await client.stop()
})

test('workspace day uses its timezone on both sides of UTC midnight', () => {
  assert.equal(dayKey(new Date('2026-10-23T17:00:00Z'), 'Asia/Shanghai'), '2026-10-24')
  assert.equal(dayKey(new Date('2026-10-24T23:00:00Z'), 'America/Los_Angeles'), '2026-10-24')
})
