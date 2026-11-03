# Cloud Run deployment

Cloud Run uses webhooks; local development uses polling. Deploying this service
can incur Google Cloud charges. Free allowances and maximum instances are not
a guaranteed zero-cost limit. Configure billing controls before deployment.

## Local checks

```bash
python -m pip install -r requirements.txt
python -m unittest -v test_cloud_run
```

The tests mock Google and Telegram; they do not register webhooks or send messages.
For polling, leave `BOT_MODE=polling` in your ignored `.env`.

## Google configuration

Use the existing project and enable Cloud Run, Cloud Build, Artifact Registry,
Secret Manager and Sheets APIs. Give the build identity the Cloud Run Builder
role if required by the source deployment flow. Use a dedicated runtime service
account (the existing spreadsheet reader is suitable), with Viewer access to the
spreadsheet and Secret Manager Secret Accessor access to these two secrets only:

- `futsal-bot-token`: the Telegram token.
- `futsal-webhook-secret`: a random secret generated with
  `python -c "import secrets; print(secrets.token_hex(32))"`.

The runtime account does not need project-wide Editor or Owner access.
Do not upload the Google JSON key: the application uses the attached service
account when `GOOGLE_APPLICATION_CREDENTIALS` is unset. Local JSON authentication
continues to work. Share the spreadsheet with the exact runtime account email.

## Deploy the prepared repository

Commit and push the code, then clone or pull it in Cloud Shell. Select the correct
project with `gcloud config set project YOUR_PROJECT_ID`.

In the Cloud Shell editor, create `/tmp/futsal-cloud-env.yaml` outside the
repository with your settings (replace the example values):

```yaml
BOT_MODE: "webhook"
PLAYERS_SOURCE: "google_sheets"
WHITELISTED_CHAT_IDS: "YOUR_ALLOWED_CHAT_IDS"
GOOGLE_SHEETS_SPREADSHEET_ID: "YOUR_SPREADSHEET_ID"
GOOGLE_SHEETS_RANGE: "'Players'!A:Z"
```

Run `chmod 600 /tmp/futsal-cloud-env.yaml`. This file contains private settings,
not the Telegram secrets; never add it to Git. Then run the following from the
repository directory, replacing the runtime account address:

```bash
gcloud run deploy team-balancer-service \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --service-account YOUR_RUNTIME_ACCOUNT_EMAIL \
  --cpu 1 \
  --memory 512Mi \
  --min-instances 0 \
  --max-instances 1 \
  --concurrency 1 \
  --timeout 60 \
  --cpu-throttling \
  --env-vars-file /tmp/futsal-cloud-env.yaml \
  --set-secrets BOT_TOKEN=futsal-bot-token:1,TELEGRAM_WEBHOOK_SECRET=futsal-webhook-secret:1
```

Keep the same variable values in Cloud Run for subsequent deployments. Leave
`GOOGLE_APPLICATION_CREDENTIALS` unset. Cloud Run supplies `PORT` automatically.
Use request-based billing, minimum instances zero, and a service-level maximum of
one where available. These settings limit resources, not total charges.

After deployment, open `https://YOUR_SERVICE_URL/health` and check for `status: ok`.
Avoid paths ending in `z`: Cloud Run reserves some of them and can return a
frontend 404 before the request reaches the container or its request logs.
Telegram will continue using its previous delivery mode until you explicitly
register the webhook below.
The Docker image and source upload use strict allowlists: no `.env`, credential
files, private CSVs or local environments are uploaded.

## Switch Telegram to the deployed service

Stop local polling. Set `TELEGRAM_WEBHOOK_SECRET` in your ignored local `.env` to
the same value as the cloud secret. Keep the existing local `BOT_TOKEN`.

```bash
python manage_webhook.py set --url https://YOUR_SERVICE_URL/telegram
python manage_webhook.py status
```

This registers only message updates, preserves queued updates, and tells Telegram
to use one connection. Registration is explicit and never happens during startup.
Public Cloud Run access is needed for Telegram; the endpoint checks the webhook
secret before parsing requests, then checks the allowed chat IDs. It processes
commands before returning HTTP success so request-based CPU allocation is safe.

Test `/start`, `/balance` and `/format`, then test again after an idle period.
Check Cloud Run logs and webhook status if delivery fails. No load balancer,
custom domain, scheduled keep-alive or VPC connector is required.

## Updates and rollback

For code updates, push, pull in Cloud Shell, and redeploy the service. Use the
Console to preserve the environment variables and secret mappings; do not rerun
`--set-env-vars` without including your complete settings. If using the initial
command, keep the private environment file current.
Keep the existing public URL; webhook registration only needs changing if its
URL or secret changes. Restart/redeploy to refresh the startup-loaded roster.

To resume local polling:

```bash
python manage_webhook.py delete
```

Set `BOT_MODE=polling` locally and run `python team-balancer-bot.py`. To stop cloud
charges, also delete or disable the cloud service and review retained images and
secrets; removing a webhook alone is not a billing control.

Failed commands return an error so Telegram can retry. Successful update IDs are
remembered only in memory (up to 1024 per instance). A restart or a failure after
partially sending replies can therefore cause duplicate replies. Durable exactly-once
delivery is not implemented. Cold starts add latency, and invalid HTTP requests
still consume resources: webhook authentication is not a spending cap.
