import httpx
import pytest

from mind_research import ENGINES, Fetcher, ResearchError, assert_public_url, html_to_text, rank_passages, verify_citations

PAGE = """<html><head><title>Robot Arms</title><script>evil()</script></head>
<body><nav>menu</nav><article><h1>Servo control</h1>
<p>Hobby servos are controlled with a 50 Hz PWM signal where pulse width sets the angle between 1 and 2 milliseconds.</p>
<p>Stepper motors move in discrete steps and are commonly driven with A4988 or TMC2209 drivers.</p></article>
<footer>copyright</footer></body></html>"""


def test_html_to_text_strips_chrome() -> None:
    title, text = html_to_text(PAGE)
    assert title == "Robot Arms"
    assert "50 Hz PWM" in text and "evil" not in text and "menu" not in text and "copyright" not in text


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost:8000/x", "http://169.254.169.254/latest/meta-data", "http://10.0.0.5/", "file:///etc/passwd", "http://[::1]/"])
async def test_ssrf_blocked(url: str) -> None:
    with pytest.raises(ResearchError) as ei:
        await assert_public_url(url)
    assert ei.value.kind == "blocked"


def _client(routes: dict[str, httpx.Response]) -> httpx.AsyncClient:
    def h(r: httpx.Request) -> httpx.Response:
        return routes.get(str(r.url), httpx.Response(404))

    return httpx.AsyncClient(transport=httpx.MockTransport(h))


async def test_fetch_extracts_and_records_provenance() -> None:
    c = _client({
        "https://ex.test/robots.txt": httpx.Response(200, text="User-agent: *\nDisallow: /private"),
        "https://ex.test/a": httpx.Response(301, headers={"location": "/b"}),
        "https://ex.test/b": httpx.Response(200, text=PAGE, headers={"content-type": "text/html; charset=utf-8"}),
    })
    f = Fetcher(c, check_ssrf=False)
    page = await f.fetch("https://ex.test/a")
    assert page.final_url == "https://ex.test/b" and page.title == "Robot Arms" and len(page.sha256) == 64
    assert page.retrieved_at.endswith("+00:00")
    with pytest.raises(ResearchError) as ei:
        await f.fetch("https://ex.test/private/x")
    assert ei.value.kind == "robots"


async def test_unsupported_content_type() -> None:
    c = _client({"https://ex.test/robots.txt": httpx.Response(404), "https://ex.test/x.zip": httpx.Response(200, content=b"PK", headers={"content-type": "application/zip"})})
    with pytest.raises(ResearchError):
        await Fetcher(c, check_ssrf=False).fetch("https://ex.test/x.zip")


def test_rank_passages() -> None:
    _, text = html_to_text(PAGE)
    top = rank_passages("servo pwm pulse width", [text, "Unrelated text about cooking pasta and tomatoes in a large pot of water."])
    assert top and top[0].source_index == 0 and "PWM" in top[0].text


def test_verify_citations() -> None:
    sources = ["Hobby servos are controlled with a 50 Hz PWM signal.", "Steppers move in discrete steps."]
    good = 'Servos use "a 50 Hz PWM signal" [1], while steppers move in steps [2].'
    r = verify_citations(good, sources)
    assert r.ok and r.cited == [1, 2]
    bad = 'Servos use "a 400 Hz analog voltage signal" [1] as shown in [7].'
    r = verify_citations(bad, sources)
    assert not r.ok and r.invalid == [7] and r.unverified_quotes


async def test_search_engine_contracts() -> None:
    seen = {}

    def h(r: httpx.Request) -> httpx.Response:
        seen[r.url.host] = r
        if r.url.host == "api.search.brave.com":
            return httpx.Response(200, json={"web": {"results": [{"url": "https://a", "title": "A", "description": "d"}]}})
        if r.url.host == "api.tavily.com":
            return httpx.Response(200, json={"results": [{"url": "https://b", "title": "B", "content": "c"}]})
        return httpx.Response(200, json={"results": [{"url": "https://c", "title": "C", "content": "x"}]})

    c = httpx.AsyncClient(transport=httpx.MockTransport(h))
    assert (await ENGINES["brave"]("bk", client=c).search("q"))[0].url == "https://a"
    assert seen["api.search.brave.com"].headers["X-Subscription-Token"] == "bk"
    assert (await ENGINES["tavily"]("tk", client=c).search("q"))[0].url == "https://b"
    assert (await ENGINES["searxng"](base_url="http://searx.local", client=c).search("q"))[0].url == "https://c"
    assert seen["searx.local"].url.params["format"] == "json"
