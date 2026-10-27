# Naija Bites — Complete redesigned frontend

This archive includes the actual root `package.json`, `package-lock.json`, Next.js source files and configuration. It intentionally excludes generated `.next` and `node_modules` folders.

Extract the **naija-bites-frontend** folder, open a terminal INSIDE it and run:

```powershell
npm install
Copy-Item .env.local.example .env.local
npm run dev
```

Run your existing FastAPI backend separately on http://127.0.0.1:8000.

Optional: back up and then replace the BACKEND `system_prompt.py` with the file in `backend_update/`.

This is a local-development frontend using the existing fixed test-user backend. Not production-ready authentication.
