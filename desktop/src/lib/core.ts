import { Command } from '@tauri-apps/plugin-shell'
import { createPreviewClient } from './preview'
import { RpcClient } from './rpc-client'

export const isDesktop = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window

export const core = isDesktop ? new RpcClient({
  async spawn(events) {
    const command = Command.sidecar('binaries/gzhreader-core')
    // Tauri shell emits complete lines with their newline removed.
    command.stdout.on('data', line => events.data(`${line}\n`))
    command.stderr.on('data', line => console.warn('[core]', line))
    command.on('error', error => events.exit(new Error(String(error))))
    command.on('close', () => events.exit(new Error('本地核心已经停止')))
    return command.spawn()
  },
}) : createPreviewClient()
