# rss-digest

Once a day it reads FreshRSS, writes a digest note into an Obsidian vault on Nextcloud, and files starred
FreshRSS items and Wallabag entries into LLM-maintained topic pages. The tool is stateless apart from one
SQLite file per instance.

## Instances

Run one instance per context (`carl_pro`, `carl`). Each has its own config file
(`/etc/rss-digest/<name>.toml`, see `config.example.toml`) and its own SQLite state file, with nothing
shared. That is what keeps personal and work data apart.

The config file holds passwords and API keys: `chown rss-digest` it and `chmod 600` it.

## Install on Yunohost

```
sudo useradd --system --home /var/lib/rss-digest --create-home rss-digest
sudo git clone <repo-url> /opt/rss-digest
cd /opt/rss-digest && sudo python3 -m venv .venv && sudo .venv/bin/pip install .
sudo mkdir -p /etc/rss-digest
sudo cp config.example.toml /etc/rss-digest/carl_pro.toml   # edit it, then chown/chmod as above
sudo cp deploy/rss-digest@.service deploy/rss-digest@.timer /etc/systemd/system/
sudo systemctl daemon-reload
```

Credentials to create, then put in the config:

- FreshRSS: enable the API in Settings > Authentication, then set the API password in Profile.
- Wallabag: create an API client under API clients management (client id and secret).
- Nextcloud: Settings > Security > App passwords. `vault_path` is the vault folder in that user's files.

## Run

```
sudo systemctl enable --now rss-digest@carl_pro.timer
```

The timer fires daily at 06:00 (`Persistent=true` catches up after downtime). Check with
`journalctl -u rss-digest@carl_pro`. The first run digests only the last 24 hours.
A run that fails halfway does not advance its timestamps, so the next run retries.

## Backlog

To file everything already starred or saved, run once by hand. It stops cleanly when the
token budget (`max_tokens_per_run`) is reached; repeat until it no longer says "re-run to continue".

```
sudo -u rss-digest /opt/rss-digest/.venv/bin/rss-digest --config /etc/rss-digest/carl_pro.toml backlog
```

## Vault

On first run the tool writes `_config/interests.md`, `_config/digest-prompt.md` and
`_config/page-prompt.md` into the vault, only when missing. Edit them freely; they are never overwritten.
Topic pages live in `Topics/`, digests in `Digest/`.

If you edit an AI-written section of a topic page, the page is left untouched and listed in the digest
under "Pages not updated". It stays that way; v1 has no automatic release. Your own "My notes" section is
always preserved.

## Obsidian setup

- Settings > Files and links: attachments folder `_attachments/`, Wikilinks off (Markdown links).
- Android sync: Remotely Save (WebDAV to Nextcloud) or FolderSync.
- Q&A over the vault: Copilot for Obsidian with a custom OpenAI-compatible endpoint (the same one as
  `[llm]`). This is a recommendation only. Verify it works on Android before relying on it.
