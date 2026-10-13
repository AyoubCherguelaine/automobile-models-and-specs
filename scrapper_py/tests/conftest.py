import pytest

from automobile_scraper.storage import Store

BRANDS = """<div class="carman"><meta itemprop="url" content="/ac/">
<h2 itemprop="name">AC</h2><img itemprop="logo" src="/logo.jpg"></div>"""
FINDER = """<div class="cfrow"></div><div class="cfrow"><ul>
<li data-opt="12">AC</li><li data-opt="12">AC</li></ul></div>"""
RESULTS = '<h5><a href="/cars/ac-ace.html">AC Ace</a></h5>'
DETAIL = """<h1 class="newstitle">AC Ace &amp; Specs</h1>
<span itemprop="itemListElement">Home</span><span itemprop="itemListElement">Cars</span>
<span itemprop="itemListElement">AC</span><div class="fl newstext">
<div class="mgbot_20">Old text</div><div class="mgbot_20"><p class="x">Description</p></div>
</div><script id="schema-gallery-data" type="application/ld+json">
{"associatedMedia":[{"contentUrl":"/photo.jpg"}]}</script>
<i onclick="aeshowpress(123, 'car')"></i><div data-engid="1234">
<div class="enginedata"><div class="title"><span class="col-green">V8 6MT</span></div></div>
<div class="techdata"><div class="title">ENGINE SPECS - V8</div><table>
<tr><td>Power:</td><td>200 kW<br>300 HP</td></tr><tr><td>Ignored</td></tr>
</table></div></div>"""


@pytest.fixture
def store(tmp_path):
    result = Store(f"sqlite:///{tmp_path / 'test.db'}")
    result.initialize()
    yield result
    result.close()
