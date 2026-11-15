# Free closed beta deployment

Google Cloud project `exam-ai-beta` manages Google OAuth and the Gemini API. A PythonAnywhere Beginner account is the pilot host. Its HTTPS subdomain avoids a custom domain and its persistent user home holds SQLite. The service uses one ASGI worker. The free web app expires after a month unless renewed. Check the provider's ASGI availability and outbound proxy before enrolling users. [Free account limits](https://help.pythonanywhere.com/pages/FreeAccountsFeatures/) · [ASGI deployment](https://help.pythonanywhere.com/pages/ASGICommandLine) · [Outbound allowlist](https://www.pythonanywhere.com/whitelist/)

From the uploaded backend source directory on PythonAnywhere:

```sh
python3.11 -m venv .venv
.venv/bin/pip install --no-cache-dir -r server/requirements.txt
install -d -m 700 "$HOME/.config/examai" "$HOME/examai-state"
```

Create `$HOME/.config/examai/backend.env` with permissions 0600. Use the HTTPS subdomain for `EXAMAI_PUBLIC_URL` and the persistent absolute path for `EXAMAI_SERVER_DB`. The Google callback is `https://<username>.pythonanywhere.com/oauth/callback`. Configure `EXAMAI_GOOGLE_CLIENT_ID`, `EXAMAI_GOOGLE_CLIENT_SECRET`, and the free-tier `EXAMAI_CLOUD_GEMINI_KEY` in that file. Set `EXAMAI_ADMIN_SUBJECTS` only after confirming the two owners' OIDC subject IDs. The profile table stores their verified emails alongside those IDs after their first Google sign-in.

Register the ASGI command `/bin/sh /home/<username>/exam-ai/server/deploy/pythonanywhere.sh` for the subdomain. Use the matching absolute path for the extracted directory. The host supplies `DOMAIN_SOCKET`. The launcher disables Uvicorn access logging. Restrict access to the host's proxy logs and keep secrets and databases outside web-served paths. Track startup errors and Cloud failures in the host's error log. Back up the database consistently with SQLite's backup API.

If the free ASGI worker does not inherit the outbound proxy, set `HTTPS_PROXY=http://proxy.server:3128` and `https_proxy=http://proxy.server:3128` in its private environment file. Verify access to both the Google token and signing-certificate endpoints with a real sign-in before opening invitations. [PythonAnywhere outbound proxy](https://help.pythonanywhere.com/pages/403ForbiddenError/)

## Budget and abuse limits

For public Cloud Gemini, the server requires the following settings:

- `EXAMAI_CLOUD_DAILY_BUDGET_MICROUSD` is the daily UTC admission budget. One dollar is 1,000,000 micro-USD.
- `EXAMAI_CLOUD_RESERVE_MICROUSD` is the amount reserved for each in-flight request. Set it conservatively to cover the maximum billable attempt or attempts for the enabled models. An incomplete stream or missing provider usage leaves its reservation in place.
- `EXAMAI_CLOUD_INPUT_USD_PER_MILLION` and `EXAMAI_CLOUD_OUTPUT_USD_PER_MILLION` use rates covering both FAST models. Output includes thinking tokens. Check the rates before deploying. [Gemini pricing](https://ai.google.dev/gemini-api/docs/pricing)

The budget transaction rejects new requests with HTTP 429 before reaching Gemini when a reservation cannot fit. Completed requests settle at the estimated token charge, and the admin API shows token totals and reserved or estimated spend. Provider charges and billing for unrelated API clients sit outside this ledger. Keep the Gemini project on its free tier for this pilot. The existing per-user and project request quotas, one active request per account, two registered sessions per account, beta time and report caps also apply.

## Owner verification

1. Call `/health` over HTTPS. Sign in via the Windows build packaged for this public origin and verify the returned account.
2. After the two owners sign in, verify their Google subject IDs on the backend and set the admin allowlist. Reload the ASGI app. Open `/admin` and verify regular users cannot access `/admin/data`.
3. Find the tester's verified subject in the admin view. Select `invite`, set an expiry and give the code privately to that tester. Confirm an initial 1800 seconds and rejection of code reuse.
4. Verify a text stream and an image stream with BYOK unset. Reopen Windows, check the session and remaining minutes, grant bonus minutes, test expiry and submit a report. Redeploy once and verify the same persistent SQLite data.
