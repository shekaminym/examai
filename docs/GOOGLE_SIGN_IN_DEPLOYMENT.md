# EXAM AI Google Sign-In

## Google Cloud Console

1. Create or select a Google Cloud project. In **Google Auth Platform → Branding**, enter the app name, support email and developer contacts.
2. Under **Audience**, choose **External**, keep the app in **Testing** and add the Google accounts of beta testers. The OAuth request uses `openid email`.
3. Under **Clients**, create an OAuth client of type **Web application**. Set its authorized redirect URI to exactly `https://beta.example.org/oauth/callback`, with your actual hostname. Store the ID and secret in the backend's secret settings. [Google OAuth for web servers](https://developers.google.com/identity/protocols/oauth2/web-server) · [Google OpenID Connect](https://developers.google.com/identity/openid-connect/openid-connect)

## HTTPS backend with persistent data

Use a VM and a local persistent disk for this SQLite release. The active-request lock is process-local, so run one worker. Cloud Run's local filesystem is ephemeral. Moving to Cloud Run would need a durable transactional database and a shared request lock. [Cloud Run storage](https://docs.cloud.google.com/run/docs/securing/security)

Point the hostname at the VM. Open ports 80 and 443 to the reverse proxy, and leave the API on loopback. Create an unprivileged `examai` service user. Deploy the reviewed sources to `/opt/examai`, then create the virtual environment:

```sh
cd /opt/examai
python3 -m venv .venv
.venv/bin/pip install -r server/requirements.txt
```

Store these settings in the host secret manager or in root-owned `/etc/examai/backend.env` with mode `0600`:

```dotenv
EXAMAI_PUBLIC_URL=https://beta.example.org
EXAMAI_SERVER_DB=/var/lib/examai/beta.sqlite
EXAMAI_GOOGLE_CLIENT_ID=your-web-client-id
EXAMAI_GOOGLE_CLIENT_SECRET=your-web-client-secret
EXAMAI_CLOUD_GEMINI_KEY=your-server-gemini-key
EXAMAI_ADMIN_SUBJECTS=verified-owner-google-subjects
```

Run the service using `/etc/systemd/system/examai.service`:

```ini
[Unit]
Description=EXAM AI beta API
After=network-online.target

[Service]
User=examai
Group=examai
WorkingDirectory=/opt/examai
StateDirectory=examai
EnvironmentFile=/etc/examai/backend.env
ExecStart=/opt/examai/.venv/bin/python -m uvicorn server.api:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true

[Install]
WantedBy=multi-user.target
```

Example Caddy site:

```caddyfile
beta.example.org {
    request_body {
        max_size 4MB
    }
    reverse_proxy 127.0.0.1:8000 {
        flush_interval -1
    }
}
```

Validate the reverse proxy, start the service and check `https://beta.example.org/health` through a valid certificate. Keep access logs free of OAuth callback query strings. Back up SQLite with its backup API or during service downtime. Set upstream Gemini project quotas before distribution. The API enforces license checks, active sessions, 30 daily requests or 60 weighted units per user and 300 units globally. Exact token spend accounting and a hard currency cap remain open. [Caddy reverse proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy) · [Caddy request body limit](https://caddyserver.com/docs/caddyfile/directives/request_body)

## Windows package and end-to-end check

```powershell
.\release\build.ps1 -CloudUrl https://beta.example.org
```

Only the public HTTPS address goes into the package. OAuth and Gemini credentials stay on the server.

1. Install the package. In **Settings → General**, choose **Continue with Google** and complete consent in the system browser. The server callback confirms authorization. The app polls for a single-use claim and stores the session in Windows DPAPI.
2. Confirm the Google email in Settings and reopen the app to check session persistence.
3. From `/admin`, issue a beta invite for the verified Google subject. Redeem it in the app and confirm the 30-minute allowance. An ordinary user must receive HTTP 403 on admin data.
4. With the personal Gemini key unset, send a text and a screen request and check streaming. Sign out, then verify that the app still works with a personal key in **AI & Models**.
