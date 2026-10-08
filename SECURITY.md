# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| `main`  | :white_check_mark: |

## Reporting a Vulnerability

We take the security of this project seriously. If you discover a security vulnerability, please do **not** report it through public GitHub issues, pull requests, or discussion forums.

### Reporting Process

1. **GitHub Private Vulnerability Reporting**: Please report security vulnerabilities via GitHub's private vulnerability reporting feature:
   - Navigate to the **Security** tab of the repository on GitHub.
   - Click **Report a vulnerability** to open an advisory draft.

2. **Credentials and Secrets**:
   - Never commit or submit API keys, tokens, or credentials in issues, pull requests, or bug reports.
   - If you accidentally expose credentials, revoke and rotate them immediately with the respective provider (e.g., Alpaca Markets).
   - Use `.env` files for local credentials; these are excluded from version control via `.gitignore`. A template is provided in `.env.example`.

### Response Timeline

- You should receive an acknowledgment within 48 hours.
- We will provide a status update and estimated timeline for a fix once the report has been validated.
- Public disclosure will be coordinated after a fix is merged.
