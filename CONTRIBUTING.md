# Contributing to Antigravity Orchestrator

Thank you for your interest in contributing to **Antigravity Orchestrator**! We welcome community contributions, bug reports, feature suggestions, and documentation enhancements.

---

## Code of Conduct

We are committed to providing a friendly, safe, and welcoming environment for all contributors, regardless of experience level, gender identity, sexual orientation, disability, personal appearance, race, ethnicity, or religion.

Please treat everyone with respect, kindness, and empathy.

---

## Getting Started

### 1. Prerequisites
- **Operating System**: Windows 10 or 11 (64-bit)
- **Python**: 3.10, 3.11, 3.12, 3.13, or 3.14
- **Google Antigravity**: Installed and executable
- **Git**: Installed and configured

### 2. Fork & Clone
1. Fork the repository on GitHub:
   ```bash
   https://github.com/mohsinkhandevs/Antigravity-Orchestrator
   ```
2. Clone your fork locally:
   ```bash
   git clone https://github.com/<your-username>/Antigravity-Orchestrator.git
   cd Antigravity-Orchestrator
   ```

### 3. Create a Virtual Environment
```bash
python -m venv .venv
# PowerShell:
.\.venv\Scripts\Activate.ps1
# Or Command Prompt:
.\.venv\Scripts\activate.bat
```

### 4. Install Dependencies
```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
```

---

## Local Development & Testing

### Running the App from Source
```bash
python main.py
```

### Running the Test Suite
The project includes a comprehensive suite of 280+ automated unit and integration tests.

Run all tests:
```bash
python -m pytest
```

Run specific test files:
```bash
python -m pytest tests/test_cdp_controller.py
python -m pytest tests/test_token_generation_speed.py
```

Run with code coverage:
```bash
python -m pytest --cov=app --cov=cdp
```

### Packaging the Standalone Executable
You can build the Windows `.exe` locally using PyInstaller:

```bash
# Directory Bundle (recommended for fast iteration)
python build_exe.py --onedir

# Single Executable (.exe)
python build_exe.py --onefile
```
The output binary will be generated under `dist/Antigravity_CDP_Studio.exe`.

---

## Privacy & Security Rules for Contributions

> [!CAUTION]
> **Never commit real user credentials, account emails, tokens, or private workspace paths.**

- Always use dummy mock emails (e.g. `testuser1@example.com`, `account1@gmail.com`) in unit tests and test fixtures.
- Ensure your personal `accounts.json`, `config.json`, and `queue.json` files remain ignored by `.gitignore`.
- Run `git status` before committing to verify no untracked secrets or session data are staged.

---

## Submitting Pull Requests

1. **Create a branch**:
   ```bash
   git checkout -b feat/your-feature-name
   # or
   git checkout -b fix/your-bugfix-name
   ```
2. **Make your changes**: Keep commits focused and atomic.
3. **Run tests**: Verify that `python -m pytest` passes completely.
4. **Push your branch**:
   ```bash
   git push origin feat/your-feature-name
   ```
5. **Open a Pull Request**: Submit your PR targeting `main` on GitHub, describing the motivation, implementation details, and verification steps.

---

## Need Help?
Feel free to open an issue or start a discussion on GitHub if you have questions or ideas!
