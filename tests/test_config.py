import pytest

from rss_digest.config import ConfigError, load_config

MINIMAL = """
state_path = "/var/lib/rss-digest/carl_pro.db"

[freshrss]
url = "https://rss.example"
user = "carl_pro"
api_password = "p"

[wallabag]
url = "https://wb.example"
client_id = "id"
client_secret = "sec"
username = "carl_pro"
password = "p"

[nextcloud]
url = "https://nc.example"
user = "carl_pro"
app_password = "p"
vault_path = "RSS Vault"

[llm]
base_url = "https://llm.example/v1"
api_key = "k"
cheap_model = "small"
writer_model = "big"
"""


def write(tmp_path, text):
    p = tmp_path / "c.toml"
    p.write_text(text)
    return p


def test_load_config_defaults(tmp_path):
    c = load_config(write(tmp_path, MINIMAL))
    assert c.digest.top_n == 15 and c.digest.also_notable == 10
    assert c.llm.max_tokens_per_run == 200000
    assert c.nextcloud.vault_path == "RSS Vault" and str(c.state_path).endswith("carl_pro.db")


def test_missing_key_names_it(tmp_path):
    with pytest.raises(ConfigError, match="llm.writer_model"):
        load_config(write(tmp_path, MINIMAL.replace('writer_model = "big"', "")))


def test_missing_file_is_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml")
