/**
 * Supabase client for Vula Dashboard.
 * Used for Auth (login) and Storage (product image uploads).
 */
import { createClient } from '@supabase/supabase-js'

const SUPABASE_URL = import.meta.env.VITE_SUPABASE_URL || ''
const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY || ''

/** False on a build without its Supabase settings: sign-in says so instead of the app failing. */
export const supabaseConfigured = Boolean(SUPABASE_URL && SUPABASE_ANON_KEY)

if (!supabaseConfigured) {
  console.error('[Vula] VITE_SUPABASE_URL / VITE_SUPABASE_ANON_KEY are not set on this build — sign-in and uploads are off.')
}

// createClient('') throws while the modules load, before React (and ErrorBoundary) exist, so a
// build missing these settings was a blank white page (9 Nov 2026 audit). A placeholder address
// lets the app render; every call to it fails, and sign-in says why.
export const supabase = createClient(SUPABASE_URL || 'https://supabase-not-configured.invalid',
                                     SUPABASE_ANON_KEY || 'not-configured')
