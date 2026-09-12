# Deploy workflow

This repository has two separate deployment paths. Use one of them; do not put
Telegram tokens in GitHub Actions variables or in this repository.

## Recommended: deploy from GitHub to your server

1. On the server, run the one-time bootstrap (as a sudo-capable user):

   ```bash
   bash deploy.sh https://github.com/<OWNER>/<REPOSITORY>.git
   ```

2. Edit `/opt/clickmint/.env` with the three BotFather tokens and your numeric
   `OWNER_USER_ID`, then run the preflight check shown by the script. The `.env`
   file is not tracked by Git.

3. Install the service files and start all three bots. `deploy.sh` installs the
   reward, partnership, and admin units. Verify them with:

   ```bash
   sudo systemctl status clickmint-reward clickmint-partner clickmint-admin
   ```

### Optional automatic deploy on push

`.github/workflows/deploy.yml` is deliberately opt-in. Add these repository
secrets under **Settings → Secrets and variables → Actions**:

- `DEPLOY_HOST`: server hostname or IP
- `DEPLOY_USER`: SSH user (normally `clickmint`)
- `DEPLOY_KEY`: private SSH key whose public half is in that user's
  `~/.ssh/authorized_keys`
- `DEPLOY_PORT`: optional SSH port, default `22`

The workflow runs only for pushes to `main`. It connects to `/opt/clickmint`,
pulls with `--ff-only`, updates the virtual environment, and restarts the reward and partnership
services. Because the checked-in workflow is intentionally conservative, restart
the admin service separately after a deploy:

```bash
sudo systemctl restart clickmint-admin
```

A pull request does not deploy.

Before enabling it, confirm that the server has:

- the repository cloned at `/opt/clickmint`;
- `/opt/clickmint/.env` owned by the service user and populated with real values;
- passwordless `sudo systemctl restart clickmint-reward clickmint-partner
  clickmint-admin` for the deploy user (or adjust the workflow for your host);
- the service files installed and enabled.

## Manual restart

After a normal `git pull`, update dependencies and restart explicitly:

```bash
cd /opt/clickmint
.venv/bin/pip install -r requirements.txt
sudo systemctl restart clickmint-reward clickmint-partner clickmint-admin
sudo journalctl -u clickmint-reward -f
```

## Safety notes

- Never put tokens in workflow YAML, commits, issue comments, or build logs.
- GitHub Actions secrets are for the SSH connection only; the bot secrets stay
  on the server in `.env`.
- If preflight fails, do not restart the services. Fix `.env` and run
  `python3 preflight.py` again first.
- The workflow is not a hosted bot service. The server must remain online and
  able to make outbound HTTPS connections to Telegram.
