"""Tap-to-pay (KakEnBetaal): NFC tag -> WhatsApp PAY message -> tip -> hosted checkout -> slip.

`vula.tap.core` is pure (no I/O, no clock reads, no DB) so every rule is unit/property tested.
Everything that touches Supabase, WhatsApp or a payment gateway lives outside `core` and calls it.
"""
