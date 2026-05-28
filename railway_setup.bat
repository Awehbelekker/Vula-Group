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

echo Setting environment variables...

railway variables set SUPABASE_URL=https://jzccetzmahpcoiqwlljm.supabase.co
railway variables set SUPABASE_SERVICE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imp6Y2NldHptYWhwY29pcXdsbGptIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3OTk3MDk0MCwiZXhwIjoyMDk1NTQ2OTQwfQ.aLeOawwaXSklUxqdkPhShfb2KRibhm-aGgtuCwMnGjU
railway variables set QDRANT_BASE=https://b184d619-61db-4633-95fc-32e7761d7105.sa-east-1-0.aws.cloud.qdrant.io
railway variables set QDRANT_API_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhY2Nlc3MiOiJtIiwic3ViamVjdCI6ImFwaS1rZXk6OTBhODZlM2YtMmQwZi00YmJkLWFjMDgtZjlhYjQ1MDM2NTQ3In0.65mLi8zFvVZUfxXGae_X9PmntVJQjQKbScJamADbTNY
railway variables set OLLAMA_BASE=https://openrouter.ai/api/v1
railway variables set MODEL_WORKER=deepseek/deepseek-r1-distill-llama-70b
railway variables set MODEL_EMBED=text-embedding-3-small
railway variables set API_KEY=0d409e634bd4e81a0d7dd0764264db6cac20721e2ce43915a43cd1997a019ca5
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
pause
