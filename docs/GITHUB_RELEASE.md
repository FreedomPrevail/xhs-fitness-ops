# GitHub Release Checklist

This repository copy is sanitized for GitHub, but publishing still requires an owner decision and a final local review.

## 1. Decide repository visibility and license

- Private repository: suitable for personal use and backups; an open-source license is not required.
- Public source-visible repository without a license: people can view and fork on GitHub, but no general permission to copy, modify or redistribute is granted.
- Public open-source repository: choose a license such as MIT, Apache-2.0 or GPL only after understanding its permissions and obligations.

Do not publish until you have made this choice. Add the selected `LICENSE` file at the repository root.

## 2. Set owner-facing metadata

Before publishing, replace placeholders in:

- `config/persona.yaml`;
- `SECURITY.md` private contact instructions;
- repository description and topics;
- optional screenshots or example output.

Only use screenshots and examples that contain no account names, analytics, note IDs, unpublished copy, personal writing or browser details.

## 3. Run the local release checks

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
```

Start the dashboard and check the main read-only endpoints:

```bash
.venv/bin/python app.py
```

Then open `http://127.0.0.1:5000`.

## 4. Check privacy and secrets

At minimum, inspect the candidate tree with:

```bash
git status --short
git diff --cached --stat
git diff --cached
```

Search filenames and source for common private markers before the first push:

```bash
rg -n '/Users/|C:/Users/|Library/Containers|Desktop/' .
rg -n -i 'api[_-]?key|client_secret|access_token|refresh_token|cookie|authorization' .
```

Keyword matches in source code and documentation are expected; verify that every value is a placeholder or variable name, never a live credential.

Do not add:

- `.env` or shell history;
- `.venv`, `.idea`, `.vscode`, caches or logs;
- `data/*.db`, `data/raw`, personal knowledge or daily decisions;
- generated drafts, covers, carousel pages or Canva packages;
- Chrome profiles, OpenCLI state, cookies or screenshots of logged-in pages;
- Canva Client Secrets, OAuth tokens or temporary edit URLs;
- archive files containing any of the above.

## 5. Keep the repository small

Do not commit the original 100+ MB distribution ZIP or runtime media. GitHub warns for files over 50 MiB and blocks normal Git files over 100 MiB. Use a GitHub Release asset only for a deliberately prepared distribution archive, or avoid distributing the archive entirely.

Official guidance: <https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github>

## 6. Initialize and push

Run these commands only after reviewing the entire candidate tree:

```bash
git init
git branch -M main
git add .
git status --short
git commit -m "Initial public release"
git remote add origin <your-repository-url>
git push -u origin main
```

If GitHub blocks a secret, do not bypass the warning. Remove the secret from the commit and rotate it if it was real.

## 7. Enable repository protections

For a public repository, enable:

- secret scanning and push protection;
- Dependabot alerts and dependency updates;
- branch protection for `main` if accepting contributions;
- private vulnerability reporting or a private security contact;
- GitHub Actions with read-only default permissions.

Official security settings: <https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-security-and-analysis-settings-for-your-repository>

## 8. Tag releases

Use semantic, non-ambiguous names instead of `final 3`:

```bash
git tag -a v4.2.0 -m "GitHub-ready release"
git push origin v4.2.0
```

Create later versions with new tags rather than overwriting an old release artifact.

## 9. Third-party and content rights

Before public distribution, confirm that you have the right to redistribute every included source file, font, template, icon, image and copied code fragment. Keep generated or licensed assets out of the repository unless their terms permit redistribution. Attribute third-party code when required by its license.

This project automates a local workflow but does not grant permission to reuse platform content or bypass platform terms. Repository users remain responsible for account safety, content rights and compliance with current official rules.

