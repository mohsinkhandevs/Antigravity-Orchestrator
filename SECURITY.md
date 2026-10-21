# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.x.x   | :white_check_mark: |
| < 1.0.0 | :x:                |

---

## Reporting a Vulnerability

We take the security of Antigravity Orchestrator and its users seriously. If you discover a security vulnerability, please do NOT create a public issue on GitHub.

Instead, please report the vulnerability confidentially via:
- **GitHub Private Vulnerability Reporting**: Use the "Report a vulnerability" button under the **Security** tab of the repository.
- **Maintainer Contact**: Send an email with details, proof of concept, and impact to the repository maintainer.

You will receive an acknowledgment within 48 hours, followed by updates on the investigation and release timeline.

---

## Security Model & Safeguards

Antigravity Orchestrator is architected around strict privacy and safety guarantees:

1. **Zero Credential Capture**:
   - The application **never** captures, requests, or stores Google passwords or master tokens.
   - Account switching is automated through native browser DOM and OCR over the official Google OAuth account chooser UI.

2. **Strictly Local Loopback**:
   - All Chrome DevTools Protocol (CDP) communications and single-instance IPC occur strictly over local loopback (`127.0.0.1`) and local named IPC pipes.
   - No telemetry, analytics, or user prompts are transmitted to third-party external servers.

3. **Data Isolation & Secrets Protection**:
   - Configuration files containing user account lists (`accounts.json`), personal settings (`config.json`), and custom prompt queues (`queue.json`) are strictly excluded from version control in `.gitignore`.
   - Never commit sensitive configuration files. Always use template files (`accounts.example.json`, `config.example.json`, `queue.example.json`).

4. **Emergency Kill-Switch**:
   - The application registers a global Windows hotkey (`F10`) that immediately terminates all background automation threads and releases browser focus.
