# EXAM AI 3.2 integration

The supplied `EXAM_AI_3_SOURCE_WITH_REQUIREMENTS.zip` is the desktop reference. The integration starts at `60364e5` (3.1, PR #2). The archive shares its original server with `4138b3c` (3.0) and has newer desktop changes. GitHub `main` at `3a16b27` has the 2.4 code and the contributor attribution.

| Area | Source used in 3.2 |
|---|---|
| Header, flags, Ghost and pin controls, question composer, window outline | Archive `ui_main.py`, `ghost_controls.py`, `rounded_window.py`, `window_mode.py`. The icon asset was already identical. |
| Settings, Compact Dock, Live Cards, themes and subscriptions | Archive Settings layout, opacity controls, navigation, subscription cards and translations. Shared Compact Dock/Live Cards implementation. The 3.1 native caption and reporting controls are applied in the archive dialog. Internal tools and sandbox checkout require developer mode. |
| FAST, dialog memory and Notes | Archive controller, prompt, conversation, document profiles, routing and line lookup. Added explicit references such as “другого виду” to the existing follow-up detector. |
| Startup and capture | Archive background database preparation and audio-device scan, and taskbar handling that keeps the HWND. Audio capture and STT modules matched the 3.1 base. |
| Accounts, BYOK and Cloud controls | 3.1 client, DPAPI credential protection, server session and Cloud transport. The archive controller retains the shared Cloud acquisition and streaming path. |
| Licenses, 30-minute beta, admin, reports and payments | 3.1 server, including invite expiry, replay-safe grants, private report attachments, and the shared LiqPay sandbox. |
| Updates and packaging | Shared signed manifests and existing PyInstaller/Inno Setup packaging, versioned for 3.2. |

The app and backend keep their existing storage paths. On first open, existing Notes get a local topic index. Old text PDFs can be reimported to obtain page citations. Keep a backup of SQLite and any WAL sidecars before upgrading a shared Notes folder. Backend schema and update verification are unchanged from 3.1.

See the [beta deployment guide](EXAMAI_3_BETA.md) for service credentials, developer flags and the signed installer workflow.
