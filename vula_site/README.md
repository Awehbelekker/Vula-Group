# vula_site — vula-ai.com

Vula's public website: one static page (`index.html`, no build step) deployed on Vercel.
The sign-up form posts to the API (`POST /v1/website/leads`, vula_mind/vula/api/website_leads.py),
which stores the lead and WhatsApps the Vula team.
