# Production deployment

This repository is validated in CI against Frappe v15 and ERPNext v15.

## Release gate

A production candidate is acceptable only when all GitHub Actions jobs pass:

- Backend static checks
- Frontend production build
- Frappe v15 install and migrate
- Production smoke assertions

The smoke test installs the app on a real Frappe site, validates Foundry Agent
fixtures, and verifies row-level isolation between two chatbot users.

## First installation

From the target bench:

```bash
cd ~/frappe-bench
bench get-app https://github.com/AlazabDev/ai_chatbot
bench --site <site> install-app ai_chatbot
bench build --app ai_chatbot
bench --site <site> clear-cache
bench --site <site> clear-website-cache
bench restart
```

ERPNext is a required app and must already be installed on the site.

## Upgrade

Take a database/files backup before changing the deployed revision:

```bash
cd ~/frappe-bench
bench --site <site> backup --with-files
cd apps/ai_chatbot
git fetch --all --prune
git checkout <approved-release-tag-or-sha>
cd ../..
bench setup requirements --python
bench build --app ai_chatbot
bench --site <site> migrate
bench --site <site> clear-cache
bench --site <site> clear-website-cache
bench restart
```

## Post-deploy checks

```bash
cd ~/frappe-bench
bench --site <site> list-apps | grep -x ai_chatbot
bench --site <site> migrate
bench doctor
sudo supervisorctl status
```

Verify the web route `/ai-chatbot` and send one normal chat message with a
non-System-Manager user.

## Azure AI Foundry

Foundry Agent seed records intentionally install disabled and with an empty
environment-specific assistant ID. Configure `foundry_assistant_id` on the
target environment, then enable only the agents that are actually deployed.

The application rejects enabling a Foundry Agent without an assistant ID.

## Write operations

ERP writes use the proposal/confirmation path. Direct legacy create/update tool
modules are intentionally not part of the repository.

A write must flow through:

1. proposal generation
2. user-bound confirmation token
3. explicit confirmation
4. permission validation
5. ERPNext document operation

Scheduled reports exclude the generic `operations` and private-file `idp`
tool categories from autonomous execution.

## Files and chat data

Uploads are private Frappe File records attached to the owning conversation.
File access is rechecked against Frappe permissions before bytes are read.
Conversation and message DocTypes enforce row-level ownership for regular
users; Administrator/System Manager retain administrative visibility.

## Rollback

Application migrations are not treated as reversible code changes. If a release
must be rolled back after a schema/data migration, restore the pre-deploy
database/files backup and deploy the previously approved application revision.
