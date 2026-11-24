import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

// PayApp.jsx is JSX, so the function is read out of the file and run on its own.
const src = readFileSync(new URL('../../src/pay/PayApp.jsx', import.meta.url), 'utf8')
const body = src.slice(src.indexOf('export function darkEnough'), src.indexOf('const buzz'))
const darkEnough = new Function(`${body.replace('export function', 'function')}; return darkEnough`)()

test("a business colour is used only when white text reads on it", () => {
  for (const dark of ['#2C5545', '#8a3b12', '#0a5d7d', '#000000', '#b91c1c']) assert.equal(darkEnough(dark), true, dark)
  for (const pale of ['#ffffff', '#ffd400', '#7fc4a6', '#f6f5f2', '#00ff00']) assert.equal(darkEnough(pale), false, pale)
  for (const bad of ['', null, 'red', '#fff', '#12345g']) assert.equal(darkEnough(bad), false, String(bad))
})
