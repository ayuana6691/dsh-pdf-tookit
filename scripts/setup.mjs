#!/usr/bin/env node
import { spawnSync } from 'node:child_process'
import { join, resolve } from 'node:path'
import { root, runtimeDir } from '../runner.js'
const args = process.argv.slice(2)
function option(name, fallback) { const i = args.indexOf(name); if (i < 0) return fallback; if (!args[i+1]) throw new Error(`${name} requires a value`); return args[i+1] }
const python = option('--python', process.platform === 'win32' ? 'python' : 'python3')
const venv = resolve(option('--venv', runtimeDir()))
function run(executable, argv) {
  const r = spawnSync(executable, argv, { stdio: 'inherit', shell: false })
  if (r.error) throw r.error
  if (r.status !== 0) process.exit(r.status || 1)
}
run(python, ['-m', 'venv', venv])
const runtime = join(venv, process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python')
run(runtime, ['-m', 'pip', 'install', '-r', join(root, 'python/requirements.txt')])
console.log(`Python runtime ready: ${runtime}`)
console.log('If --venv overrides the default shared location, set config.python to that absolute path, or set DSH_PDF_PYTHON before starting Harness.')
