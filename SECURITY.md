# Security Policy

## Supported use

This project is designed for a single operator on `127.0.0.1`. Do not expose the Flask dashboard directly to a LAN or the public internet. It has no multi-user authentication layer.

## Secrets

Never commit or share:

- LLM API keys;
- Canva Client Secrets, access tokens or refresh tokens;
- OpenCLI browser state, cookies or Chrome profiles;
- Xiaohongshu account exports or creator-center snapshots;
- personal writing samples, drafts or unpublished media;
- temporary Canva edit URLs.

The dashboard stores a Base URL key only in the current tab's `sessionStorage`. Canva credentials and OAuth tokens use the operating-system credential vault. Treat screenshots, logs and support bundles as potentially sensitive.

If a secret reaches Git history, deleting the working-tree file is not enough. Revoke or rotate the credential first, then remove it from Git history before publishing.

## Platform account safety

Connected reads reuse a real browser login. Keep actions operator-triggered, use low frequency, and stop on login verification, rate limits, security warnings or account restrictions. Do not add anti-detection, identity rotation, CAPTCHA bypass, simulated-human interaction or unattended publishing.

## Reporting a vulnerability

Do not post live secrets, private account data or an exploitable proof-of-concept in a public issue. Contact the repository owner privately and include:

- affected version or commit;
- impact and prerequisites;
- minimal reproduction with secrets removed;
- suggested mitigation, if known.

The repository owner should add a private security contact before making the repository public.

