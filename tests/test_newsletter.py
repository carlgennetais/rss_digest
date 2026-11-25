from conftest import item
from rss_digest.newsletter import split

BLURB = "A paragraph long enough to say what the linked article is about."
ISSUE = f"""
<p>This week's issue sponsored by <a href="https://ad.example/x">our friends at Acme Data Tools</a>.</p>
<h3>Posts</h3>
<p><a href="https://t.example/1">First article about query engines</a></p><p>{BLURB} See also <a href="https://ex.com/inline">this other related thing</a> inline.</p>
<li>1. <a href="https://t.example/2">Second article about agents today</a> {BLURB}</li>
<p><a href="https://t.example/s">Ranked AI work, the benchmark*</a></p><p>{BLURB}</p>
<p><a href="https://t.example/3">Third article on semantic layers</a></p><p>{BLURB}</p>
<p><a href="https://t.example/cta">Download the white paper now</a></p>
<h3>Last Issue's Top Links</h3>
<p><a href="https://t.example/old">An old article from last week</a> {BLURB}</p>
<p><a href="https://kill-the-newsletter.com/feeds/x">Kill the Newsletter! feed settings</a></p>
"""


def test_split_keeps_titled_links_with_a_blurb():
    issue = item("Weekly 12", feed="Data Weekly", id="freshrss:n")
    out = split(issue, ISSUE, lambda u: u.replace("t.example", "real.example"))
    assert [(i.id, i.url, i.title) for i in out] == [
        ("freshrss:n#0", "https://real.example/1", "First article about query engines"),
        ("freshrss:n#1", "https://real.example/2", "Second article about agents today"),
        ("freshrss:n#2", "https://real.example/3", "Third article on semantic layers"),
    ]
    assert out[0].text.startswith(BLURB) and "inline" in out[0].text and out[0].feed == "Data Weekly"
    assert all(i.published == issue.published for i in out)


def test_split_keeps_an_issue_with_too_few_articles_whole():
    issue = item("Invite")
    html = f'<p><a href="https://t.example/1">Save my free seat today</a></p><p>{BLURB}</p>'
    assert split(issue, html, lambda u: 1 / 0) == [issue]
