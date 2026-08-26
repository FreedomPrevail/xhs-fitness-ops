# Canva handoff

The project always builds a layered PPTX that can be handed to Canva. The default path is manual upload; the international Canva Connect API is an optional convenience.

## Default: manual PPTX import

After a carousel is generated, find:

```text
content/canva_packages/<post_id>/<post_id>_Canva可编辑.pptx
```

Upload this single file through Canva's “Import file” action. Check page count, Chinese font substitution, wrapping, image crop and editable objects before exporting. Uploading only the JPG previews produces flattened pages and does not preserve editable text layers.

This path requires no Developer integration, MFA, Client ID or Client Secret. It is the most portable handoff for both international Canva and Canva China, subject to each product's current PPTX import behavior.

## Optional: international Canva Connect API

This integration sends the layered PPTX to the official Canva Connect Design Import API and returns a temporary edit link:

```text
Topic / Content / Outline / Cover
  → no-text assets + local Chinese layout
  → layered PPTX
  → operator-triggered Canva Design Import
  → editable design link
```

It never publishes to Xiaohongshu and never runs on page load or a timer.

### Canva Developer Portal

1. Sign in to <https://www.canva.com/developers/integrations>.
2. Enable MFA for the international Canva account.
3. Create a Connect API integration.
4. Save the Client ID and generate a Client Secret.
5. Enable only `design:content:write`.
6. Add this authorized redirect:

   ```text
   http://127.0.0.1:8765/oauth/callback
   ```

Use `127.0.0.1`, not `localhost`. The current implementation targets `www.canva.com` and `api.canva.com`; a Canva China partnership credential is not a drop-in replacement.

### Save credentials locally

macOS:

```bash
bash setup_environment.sh
.venv/bin/python -m canva_connect configure --client-id "<client-id>"
```

Windows:

```bat
.venv\Scripts\python.exe -m canva_connect configure --client-id "<client-id>"
```

Omit `--client-secret`; the command prompts without echoing the value. Credentials go to the operating-system credential vault, not the repository.

### Authorize and check

macOS:

```bash
.venv/bin/python -m canva_connect login
.venv/bin/python -m canva_connect status
```

Windows:

```bat
.venv\Scripts\python.exe -m canva_connect login
.venv\Scripts\python.exe -m canva_connect status
```

### Import one design

```bash
.venv/bin/python -m canva_connect upload --post-id <post_id> --open
```

Or specify a PPTX:

```bash
.venv/bin/python -m canva_connect upload "/absolute/path/to/carousel.pptx" --open
```

The dashboard “发送到 Canva” button uses the same code. If the integration is not configured, keep using manual PPTX import.

## Security boundary

- Never put Client Secrets or tokens in `.env`, screenshots, prompts, manifests, packages or Git.
- Each user and computer must configure and authorize separately.
- Access and rotating refresh tokens are stored in the OS credential vault.
- Temporary Canva edit links are returned to the current process and are not persisted in the package.
- Public distribution requires a secure hosted OAuth backend and Canva review; the local callback is for the repository owner's development use.

