# One-tap connections — setting up Vula's own apps (once)

A business connects ClickUp, Google or Microsoft with one tap: it presses **Connect**, signs in to
the app and approves. That only works once Vula's own app is registered with each provider. This is
done **once for all businesses**, by the Vula operator, not by each business.

Until an app is set up, its Connect button says "can't be connected with one tap yet" (and ClickUp
offers its personal-token way instead), and the operator gets one alert. `GET /ready` lists each
one under *optional settings* as set / partly set / not set up.

Below, `API` is Vula's public API address — `PUBLIC_BASE_URL`, in production
`https://vula-group-production.up.railway.app`. Put the IDs and secrets in Railway (the vula_mind
service › Variables), then run `railway up`: a variable change alone doesn't reach running code.
Never paste a secret into a chat or a commit.

## ClickUp

1. In ClickUp: avatar › **Settings** › **Apps** › **Create new app** (needs a ClickUp workspace owner or admin).
2. Name: `Vula`. Redirect URL: `API/v1/clickup/oauth/callback`
   (production: `https://vula-group-production.up.railway.app/v1/clickup/oauth/callback`).
3. Copy the **Client ID** and **Client Secret**.
4. Railway: `CLICKUP_CLIENT_ID`, `CLICKUP_CLIENT_SECRET`. Then `railway up`.
5. Check: Settings › Connections › ClickUp shows **Connect ClickUp**; pressing it opens ClickUp and comes back "ClickUp connected".

The redirect URL must match exactly (no trailing slash). A mismatch is why, since August, the
ClickUp window never came back to Vula.

## Google (Drive)

1. https://console.cloud.google.com › create a project `Vula`.
2. **APIs & Services › Library**: enable *Google Drive API*.
3. **OAuth consent screen**: External; app name `Vula`; support email; scopes `drive.file`, `openid`,
   `email` (non-restricted — no security assessment needed); add Vula's domain; publish the app
   (in *Testing* only listed test users can connect).
4. **Credentials › Create credentials › OAuth client ID**: Web application. Authorised redirect URI:
   `API/v1/google/oauth/callback`.
5. Railway: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`. Then `railway up`.

## Microsoft (OneDrive, Outlook) and Dynamics 365

One Azure app serves both.

1. https://entra.microsoft.com › **App registrations › New registration**. Name `Vula`.
   Supported accounts: *Accounts in any organizational directory and personal Microsoft accounts*.
2. Redirect URIs (platform *Web*): `API/v1/microsoft/oauth/callback` and
   `API/v1/dynamics365/oauth/callback`.
3. **API permissions** (delegated, Microsoft Graph): `offline_access`, `User.Read`, `Files.Read`,
   `Mail.Read`, `Mail.ReadWrite`, `Mail.Send`. For Dynamics 365 add *Dynamics CRM › user_impersonation*.
4. **Certificates & secrets › New client secret**; note when it expires — Vula's check will show
   Microsoft connections as broken after that date until a new secret is set.
5. Railway: `MICROSOFT_CLIENT_ID`, `MICROSOFT_CLIENT_SECRET`. Then `railway up`.

## After setting up

- Master panel › Health › **App connections** shows every business's connections and which apps
  are still not set up; **Check now** runs the daily check straight away.
- When a connection breaks, the business's owner gets one WhatsApp with what broke and a link to
  Settings, a reminder a week later if it's still broken, and a note when it works again
  (`vula/connections.py`, migration 245).
- Mailboxes on other providers (GoDaddy, cPanel, Zoho) still connect with their password under
  Settings › Email; if Vula can't sign in, the same check tells the owner.
