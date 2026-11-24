/**
 * useBarcodeCamera.js — the camera barcode / QR reader, on its own (24 Nov 2026) so a page that only
 * scans (the door page) does not pull the stock screens' code into the dashboard's first load.
 * Moved verbatim from stock/useStock.js, which re-exports it.
 */
import { useState, useEffect, useRef, useCallback } from 'react'

function beep() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)()
    const o = ctx.createOscillator(); const g = ctx.createGain()
    o.frequency.value = 1100; g.gain.value = 0.08
    o.connect(g); g.connect(ctx.destination); o.start(); o.stop(ctx.currentTime + 0.09)
    o.onended = () => ctx.close()
  } catch { /* no audio */ }
  try { navigator.vibrate?.(60) } catch { /* no vibration */ }
}

const FORMATS = ['ean_13', 'ean_8', 'upc_a', 'upc_e', 'code_128', 'code_39', 'itf', 'qr_code']

/** The browser's BarcodeDetector (Android Chrome) or, where it's missing (iPhone Safari),
 * @zxing/browser loaded on first use. Attach videoRef to a <video muted playsInline>. */
export function useBarcodeCamera({ onCode, paused }) {
  const videoRef = useRef(null)
  const last = useRef({ code: '', at: 0 })
  const pausedRef = useRef(paused)
  const [on, setOn] = useState(false)
  const [err, setErr] = useState('')
  const [manual, setManual] = useState('')
  pausedRef.current = paused

  const emit = useCallback((code) => {
    const now = Date.now()
    // One item held in front of the camera is read many times a second — one count per 1.5s.
    if (pausedRef.current || (code === last.current.code && now - last.current.at < 1500)) return
    last.current = { code, at: now }
    beep()
    onCode(code)
  }, [onCode])

  useEffect(() => {
    if (!on) return
    let stream, timer, zxingControls, stopped = false
    ;(async () => {
      setErr('')
      try {
        if ('BarcodeDetector' in window) {
          stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } })
          if (stopped) return
          videoRef.current.srcObject = stream
          await videoRef.current.play()
          const supported = await window.BarcodeDetector.getSupportedFormats?.() || FORMATS
          const det = new window.BarcodeDetector({ formats: FORMATS.filter(f => supported.includes(f)) })
          const tick = async () => {
            if (stopped) return
            try {
              const found = await det.detect(videoRef.current)
              if (found[0]?.rawValue) emit(found[0].rawValue)
            } catch { /* frame not ready */ }
            timer = setTimeout(tick, 200)
          }
          tick()
        } else {
          const { BrowserMultiFormatReader } = await import('@zxing/browser')
          if (stopped) return
          const reader = new BrowserMultiFormatReader()
          zxingControls = await reader.decodeFromConstraints(
            { video: { facingMode: 'environment' } }, videoRef.current,
            (result) => { if (result) emit(result.getText()) })
          if (stopped) zxingControls.stop()
        }
      } catch (e) {
        setErr(e?.name === 'NotAllowedError'
          ? 'Camera permission was refused — allow it in the browser, or type the barcode below.'
          : 'Could not start the camera on this device — type the barcode below.')
        setOn(false)
      }
    })()
    return () => {
      stopped = true
      clearTimeout(timer)
      zxingControls?.stop()
      stream?.getTracks().forEach(t => t.stop())
    }
  }, [on, emit])

  const submitManual = (e) => { e.preventDefault(); if (manual.trim()) { onCode(manual.trim()); setManual('') } }
  return { videoRef, on, setOn, err, manual, setManual, submitManual }
}
