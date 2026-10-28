import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { homedir } from 'node:os'
import { fileURLToPath } from 'node:url'

export const root = dirname(fileURLToPath(import.meta.url))
export function runtimeDir() {
  return process.platform === 'win32'
    ? join(process.env.LOCALAPPDATA || join(homedir(), 'AppData/Local'), 'dsh-pdf-toolkit', 'venv')
    : join(process.env.XDG_DATA_HOME || join(homedir(), '.local/share'), 'dsh-pdf-toolkit', 'venv')
}
export function pythonPath(config = {}) {
  const local = join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  const shared = join(runtimeDir(), process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
  return config.python || process.env.DSH_PDF_PYTHON || (existsSync(local) ? local : existsSync(shared) ? shared : process.platform === 'win32' ? 'python' : 'python3')
}

export function runPython(request, config = {}, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new Error('PDF operation cancelled'))
    const timeout = config.timeoutMs ?? 180000
    const max = config.maxOutputBytes ?? 8388608
    if (!Number.isSafeInteger(timeout) || timeout < 1 || !Number.isSafeInteger(max) || max < 1024) {
      return reject(new Error('timeoutMs must be a positive integer; maxOutputBytes must be >= 1024'))
    }
    const child = spawn(pythonPath(config), ['-u', join(root, 'python/pdf_toolkit.py')], {
      stdio: ['pipe', 'pipe', 'pipe'], shell: false,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUNBUFFERED: '1' },
    })
    let stdout = '', stderr = '', bytes = 0, failure, killTimer
    const stop = (error) => {
      failure ??= error
      child.kill('SIGTERM')
      killTimer ??= setTimeout(() => child.kill('SIGKILL'), 1500)
      killTimer.unref()
    }
    const abort = () => stop(new Error('PDF operation cancelled'))
    signal?.addEventListener('abort', abort, { once: true })
    if (signal?.aborted) abort()
    const timer = setTimeout(() => stop(new Error(`PDF operation timed out after ${timeout}ms`)), timeout)
    const cleanup = () => { clearTimeout(timer); clearTimeout(killTimer); signal?.removeEventListener('abort', abort) }
    for (const [stream, kind] of [[child.stdout, 'out'], [child.stderr, 'err']]) {
      stream.setEncoding('utf8')
      stream.on('data', (text) => {
        bytes += Buffer.byteLength(text)
        if (bytes > max) return stop(new Error('PDF result exceeds maxOutputBytes; select fewer pages'))
        if (kind === 'out') stdout += text
        else stderr += text
      })
    }
    child.once('error', (error) => { cleanup(); reject(new Error(`Cannot launch PDF Python runtime: ${error.message}. Run npm run setup or set DSH_PDF_PYTHON.`)) })
    child.once('close', (code) => {
      cleanup()
      if (failure) return reject(failure)
      let data
      try { data = JSON.parse(stdout) } catch { return reject(new Error(`Invalid Python response (exit ${code}): ${stderr || stdout}`)) }
      if (code !== 0 || !data.ok) return reject(new Error(`${data.error?.type || 'PDFError'}: ${data.error?.message || stderr || 'operation failed'}`))
      resolve(data.result)
    })
    child.stdin.on('error', () => {}) // launch failures are reported by child.error/close
    child.stdin.end(JSON.stringify(request))
  })
}
