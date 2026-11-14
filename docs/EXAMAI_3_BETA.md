# EXAM AI 3.0 Closed Beta

Windows x64. The beta keeps the archived FAST-only desktop pipeline. The original source launcher still works with `python main.py`.

## Desktop build

Install Python 3.11 x64, Inno Setup 6, then run:

```powershell
python -m pip install -r requirements.txt -r requirements-rag.txt pyinstaller==6.22.3
.\release\build.ps1
```

Use `release-build/EXAM-AI-Setup.exe`. The frozen app writes its configuration and notes database under `%LOCALAPPDATA%\EXAM AI`. Source mode continues to use the working directory. To migrate legacy notes, close both versions, back up the old SQLite database (including any WAL sidecars), then import materials or copy the checkpointed database to the new data directory. Setup does not delete application data when uninstalling.

## Backend

```powershell
python -m pip install -r server/requirements.txt
python -m uvicorn server.api:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

Use one process behind HTTPS with a persistent, backed-up SQLite volume. Set request size and rate limits at the proxy and omit OAuth query strings from proxy logs. Local HTTP is accepted only at 127.0.0.1 for development. Configure these environment variables through the host secret store:

- `EXAMAI_PUBLIC_URL` is the externally reachable HTTPS server origin.
- `EXAMAI_SERVER_DB` points to the persistent SQLite database.
- `EXAMAI_GOOGLE_CLIENT_ID` and `EXAMAI_GOOGLE_CLIENT_SECRET` come from a Google Web OAuth client. Register the exact redirect URL `<public origin>/oauth/callback`. Set up the consent screen and add authorized test users.
- `EXAMAI_ADMIN_SUBJECTS` lists the confirmed Google OIDC subjects of nega71ve and shibuya, separated by commas. The server never assigns admin permissions based on a name or email. Visit `/admin`; sign in there with an authorized Google account. Its API checks permissions on every action.
- `EXAMAI_CLOUD_GEMINI_KEY` is server-only. Apply an upstream Gemini project quota and billing controls. The service limits each user to 30 requests and 60 weighted units per UTC day, and all users together to 300 weighted units. These are usage bounds, not measured costs in UAH.
- `EXAMAI_LIQPAY_SANDBOX_PUBLIC` and `EXAMAI_LIQPAY_SANDBOX_PRIVATE` enable sandbox checkout with a sandbox merchant key. Prices are 149 / 399 / 699 / 1299 UAH as in the archive. Signed callbacks record the sandbox result without granting a paid production license. There are no recurring charges. Live provider onboarding and tax/receipt requirements need separate review. See [LiqPay callbacks](https://www.liqpay.ua/doc/api/callback) and [ДПС](https://www.tax.gov.ua/nove-pro-podatki--novini-/886005.html).

In Settings → General, enter the HTTPS server address, sign in and redeem an admin-issued invite. With a personal Gemini key supplied, the desktop uses the existing direct FAST path. Without that key it uses the authenticated Cloud text/image gateway. Cloud listening uses local Whisper and a server lease. Heartbeats renew the lease every 5 seconds with a cap of 15 seconds since the last renewal. No offline cloud grace period is granted. Beta time and quotas are server-side. Billing uses bounded request weights and a single in-flight stream per account. Use a single worker process until moving the in-flight lock to a shared store.

## Updates

Configure `EXAMAI_UPDATE_URL` with the HTTPS directory that hosts manifests, and `EXAMAI_UPDATE_PUBLIC_KEY` with the trusted base64 Ed25519 public key. The private release key stays offline. Sign the completed installer with:

```powershell
python release/sign_manifest.py beta 3.0.0-beta.2 https://downloads.example/EXAM-AI-Setup.exe .\release-build\EXAM-AI-Setup.exe PATH_TO_OFFLINE_PRIVATE_KEY
```

Publish the result as `beta.json` or `stable.json`. In Settings → Diagnostics, check the selected channel and accept installation. The updater checks signature, channel, version, size and SHA-256 before opening Setup. Keep the previously tested installer for manual recovery. Automatic rollback and Authenticode signing are pending.

## Known gaps

Live Google Sign-In, Gemini Cloud, actual LiqPay sandbox callbacks and signed hosting require the service owners' environment and have not been verified. Reports currently support text and status tracking; screenshot/log attachments are pending. Sandbox payments record verification and history only; subscription entitlements, production setup, automated refunds and recurring billing remain pending. Windows EXE packaging does not bundle downloaded Whisper model weights. A clean Windows 10/11 VM and long-running desktop/hardware tests remain pending.
