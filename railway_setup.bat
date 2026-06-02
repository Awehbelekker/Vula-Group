@echo off
:: Vula Group — Railway one-shot setup
:: Run this ONCE after: railway login
:: It links the project, sets all env vars, and deploys.

echo.
echo === Vula Railway Setup ===
echo.

:: Link to Railway project (creates project if it doesn't exist)
cd /d "%~dp0"
railway link --environment production

:: ── Secrets ──────────────────────────────────────────────────────────────────
:: Secrets are NOT stored in this committed file. Set them in your shell session
:: before running this script (they are read from the environment below), e.g.:
::    set SUPABASE_SERVICE_KEY=...
::    set QDRANT_API_KEY=...
::    set API_KEY=...
:: Or paste them straight into the Railway dashboard. If any are empty this
:: script aborts so you never deploy with missing credentials.
if "%SUPABASE_SERVICE_KEY%"=="" goto :missing_secret
if "%QDRANT_API_KEY%"=="" goto :missing_secret
if "%API_KEY%"=="" goto :missing_secret

echo Setting environment variables...

railway variables set SUPABASE_URL=https://jzccetzmahpcoiqwlljm.supabase.co
railway variables set SUPABASE_SERVICE_KEY=%SUPABASE_SERVICE_KEY%
railway variables set QDRANT_BASE=https://b184d619-61db-4633-95fc-32e7761d7105.sa-east-1-0.aws.cloud.qdrant.io
railway variables set QDRANT_API_KEY=%QDRANT_API_KEY%
railway variables set OLLAMA_BASE=https://openrouter.ai/api/v1
railway variables set MODEL_WORKER=deepseek/deepseek-r1-distill-llama-70b
railway variables set MODEL_EMBED=text-embedding-3-small
railway variables set API_KEY=%API_KEY%
railway variables set WHATSAPP_VERIFY_TOKEN=vula-webhook-2026
railway variables set TEAM_WHATSAPP=27827077080
railway variables set DATA_DIR=/data
railway variables set UPLOAD_DIR=/data/uploads
railway variables set TAKEOFF_UPLOAD_DIR=/data/takeoff
railway variables set DEBUG=false

echo.
echo Remember to also set:
echo   OPENROUTER_API_KEY=sk-or-v1-...   (from openrouter.ai)
echo   WHATSAPP_PHONE_ID=...             (from Meta after registering 0673636081)
echo   WHATSAPP_TOKEN=...                (from Meta)
echo.

echo Deploying...
railway up --detach

echo.
echo Done! Check Railway dashboard for the deployment URL.
echo Set VULA_BASE_URL to your Railway URL once it is live.
goto :end

:missing_secret
echo.
echo ERROR: One or more secrets are not set in this shell session.
echo Set them first, then re-run, e.g.:
echo   set SUPABASE_SERVICE_KEY=your-key-here
echo   set QDRANT_API_KEY=your-key-here
echo   set API_KEY=your-key-here
echo.

:end
pause
