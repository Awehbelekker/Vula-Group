// lib/session.js — a lapsed sign-in sends the person back to sign in (8 Nov 2026).
import test from 'node:test'
import assert from 'node:assert/strict'
import { isLapsed, watchSession, LAPSED_EVENT } from '../../src/lib/session.js'

class FakeWindow extends EventTarget {}
globalThis.window = new FakeWindow()

function fakeAuth(session) {
  const subs = []
  return {
    subs,
    getSession: async () => ({ data: { session } }),
    onAuthStateChange: (cb) => { subs.push(cb); return { data: { subscription: { unsubscribe: () => subs.splice(subs.indexOf(cb), 1) } } } },
  }
}
const tick = () => new Promise((r) => setTimeout(r, 0))

test('a 401 is a lapsed sign-in only when the request had no token', () => {
  assert.equal(isLapsed(401, false), true)
  assert.equal(isLapsed(401, true), false)     // a real token refused: the page shows its own error
  assert.equal(isLapsed(403, false), false)
  assert.equal(isLapsed(200, false), false)
})

test('no session on start sends the person back to sign in, once', async () => {
  let n = 0
  const stop = watchSession(fakeAuth(null), () => { n += 1 })
  await tick()
  window.dispatchEvent(new Event(LAPSED_EVENT))
  assert.equal(n, 1)
  stop()
})

test('a live session stays signed in until SIGNED_OUT or a lapsed API call', async () => {
  let n = 0
  const auth = fakeAuth({ access_token: 't' })
  const stop = watchSession(auth, () => { n += 1 })
  await tick()
  assert.equal(n, 0)
  auth.subs.forEach((cb) => cb('TOKEN_REFRESHED'))
  assert.equal(n, 0)
  auth.subs.forEach((cb) => cb('SIGNED_OUT'))
  assert.equal(n, 1)
  stop()

  let m = 0
  const stop2 = watchSession(fakeAuth({ access_token: 't' }), () => { m += 1 })
  await tick()
  window.dispatchEvent(new Event(LAPSED_EVENT))
  assert.equal(m, 1)
  stop2()
})

test('after stopping, nothing fires', async () => {
  let n = 0
  const auth = fakeAuth({ access_token: 't' })
  const stop = watchSession(auth, () => { n += 1 })
  stop()
  window.dispatchEvent(new Event(LAPSED_EVENT))
  assert.equal(auth.subs.length, 0)
  assert.equal(n, 0)
})
