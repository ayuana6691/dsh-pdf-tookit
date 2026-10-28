import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { join } from 'node:path'
import { root, runPython, pythonPath } from '../runner.js'
const result = spawnSync(pythonPath(), ['-m', 'unittest', 'discover', '-s', join(root, 'test'), '-p', 'test_pdf.py', '-v'], { stdio: 'inherit' })
if (result.error) throw result.error
if (result.status !== 0) process.exit(result.status || 1)
assert.equal((await runPython({ action: 'doctor' })).ready, true)
await assert.rejects(runPython({ action: 'does-not-exist' }), /Unknown action/)
const controller = new AbortController(); controller.abort()
await assert.rejects(runPython({ action: 'doctor' }, {}, controller.signal), /cancelled/)
await assert.rejects(runPython({ action: 'doctor' }, { python: '/nonexistent/python' }), /Cannot launch/)
await assert.rejects(runPython({ action: 'doctor' }, { timeoutMs: 1 }), /timed out/)
console.log('Python functional tests and Node process/error/cancellation tests passed.')
