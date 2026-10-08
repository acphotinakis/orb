# Contributing to ORB

Thank you for your interest in contributing to the ORB quantitative research system.

## Code of Conduct

Please maintain a respectful, constructive, and collaborative environment.

## Development Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/acphotinakis/orb.git
   cd orb
   ```

2. Create and activate a Python virtual environment (Python 3.10+):
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   pip install pre-commit ruff==0.16.8 black==26.5.1 flake8==7.3.0
   pre-commit install
   ```

4. Set up environment variables:
   ```bash
   cp .env.example .env
   # Add your paper trading credentials to .env if running live data fetches
   ```

## Code Quality Standards

All pull requests must pass our automated quality checks before merging:

- **Format and Lint**:
  ```bash
  ruff check src tests --fix
  black --line-length 88 src tests
  flake8 src tests --max-line-length=88 --extend-ignore=E203,W503
  ```
- **Tests**:
  ```bash
  pytest tests/ -v
  ```
- **Pre-commit**:
  ```bash
  pre-commit run --all-files
  ```

## Security

Do **not** commit credentials, secrets, or API keys. See [SECURITY.md](SECURITY.md) for vulnerability reporting guidelines.
