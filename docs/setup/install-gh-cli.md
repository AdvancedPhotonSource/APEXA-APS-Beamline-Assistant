# Installing GitHub CLI (gh)

GitHub CLI is optional. Install it with the package manager for your workstation.

## macOS

```bash
brew install gh
```

## Windows

```powershell
winget install --id GitHub.cli -e
```

## Linux

Use the GitHub CLI installation instructions for your distribution:
<https://github.com/cli/cli/blob/trunk/docs/install_linux.md>

## Authenticate and create a pull request

```bash
gh auth login
cd /path/to/APS-Beamline-Assistant
gh pr create --base main --head <your-branch>
```

Replace `<your-branch>` with the branch containing your changes. Review the
generated title and description before submitting.
