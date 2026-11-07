# Using a real Slack workspace

About five minutes. The mock is enough for testing, but the demo video looks better in real Slack on
a phone.

1. Generate the manifest:

   ```bash
   cd demo
   ./sandbox.sh hermes slack manifest --agent-view --write
   ```

2. At <https://api.slack.com/apps>, choose **Create New App**, then **From an app manifest**, pick a
   test workspace, and paste `~/.hermes-nightshift/slack-manifest.json`.
3. Under **Socket Mode**, enable it and create an app-level token with `connections:write`. This is
   `SLACK_APP_TOKEN` (`xapp-...`).
4. Under **OAuth & Permissions**, install the app. The bot token is `SLACK_BOT_TOKEN` (`xoxb-...`).
5. Create a channel such as `#on-call`, then invite the bot with `/invite @Hermes`.
6. Find the channel id (channel details, at the bottom) and your member id (your profile, **Copy
   member ID**).
7. Run the sandbox against it:

   ```bash
   export SLACK_BOT_TOKEN=xoxb-... SLACK_APP_TOKEN=xapp-...
   export NIGHTSHIFT_SLACK_CHANNEL=C0123ABC NIGHTSHIFT_APPROVER=U0123ABC
   ./sandbox.sh setup
   ./sandbox.sh gateway
   ```

On first connect Hermes installs its Slack extra by itself. Tokens stay in
`~/.hermes-nightshift/.env`, which is private to you and not in the repository.

To go back to the mock, open a new shell without those variables and run `./sandbox.sh setup` again.
