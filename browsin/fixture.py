"""A two-page local site that makes the Phase 4 gate falsifiable.

The gate has to separate four things a single "it worked" would blur together:

1. the agent drove a tab that *already existed* (rather than opening its own),
2. the change came from a **model action**, not from browser-use's injected step 0,
3. a screenshot actually reached the model, and
4. the model **read** the screenshot rather than the DOM.

(4) is the one that needs a fixture. The nonce is painted into a `<canvas>` with
`fillText` and exists nowhere else — not in the DOM text, not in an `alt`, `title`,
`aria-label` or `data-` attribute, not in the page title, not in a filename, and not in the
URL. `document.body.innerText` does not contain it. So a model that only ever sees the
serialised DOM cannot produce it, and `assert_nonce_not_in_dom()` proves that property of
the fixture itself rather than assuming it — a fixture that leaked the nonce into the DOM
would turn the whole gate green while proving nothing.

(1) and (2) need the second page: the model has to *click* to get there, so the tab's URL
changes as a consequence of an action taken at step ≥ 1.

Served over `http://127.0.0.1` rather than `file://` on purpose. A `file://` URL has no
hostname, and browser-use's `SecurityWatchdog` closes hostname-less tabs at connect —
Chrome then exits with its last tab. It also keeps `Agent._extract_start_url` from finding
anything: `file://` URLs skip its extension filter entirely and would be injected as a
synthetic step 0, which is exactly the false pass (2) exists to catch.

Stdlib only.
"""

from __future__ import annotations

import http.server
import re
import socket
import threading
from typing import Any

#: Ambiguous glyphs removed: a 7B vision model confusing O for 0 would fail the gate for
#: legibility rather than for plumbing, which would make it a model-quality test wearing a
#: plumbing test's clothes.
NONCE_ALPHABET = 'ACEFHJKLMNPRTUVWXY34679'
NONCE_LEN = 5

START_PATH = '/start.html'
SECOND_PATH = '/second.html'

# Page one carries only the button. The nonce lives on page TWO, and that ordering is
# load-bearing rather than cosmetic.
#
# It was the other way round on the first run, and the gate measured the wrong thing: the
# model clicked Continue before reading the code, so by the time it tried to answer, the
# page holding the nonce was gone and it could only have succeeded by carrying the value in
# `memory` across a navigation. That is a question about the model's working memory, not
# about whether a screenshot reached it — and G4 exists to answer the second one. With the
# nonce on the destination page, the pixels the answer depends on are on screen at the
# moment the answer is given.
_START = """<!doctype html>
<html><head><meta charset="utf-8"><title>Browsin gate page one</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 40px; background:#fff; color:#111; }}
 a.go {{ display:inline-block; padding:18px 36px; background:#0b5; color:#fff;
        font-size:26px; text-decoration:none; border-radius:6px; }}
</style></head>
<body>
<h1>Step one</h1>
<p>The access code is on the next page. Press the button to see it.</p>
<p><a class="go" href="{second}">Continue</a></p>
</body></html>"""

_SECOND = """<!doctype html>
<html><head><meta charset="utf-8"><title>Browsin gate page two</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 40px; background:#fff; color:#111; }}
 canvas {{ border: 4px solid #111; display:block; margin: 24px 0; }}
</style></head>
<body>
<h1>Access code</h1>
<p id="marker" style="color:#888">{marker}</p>
<canvas id="c" width="620" height="200"></canvas>
<script>
 // The nonce reaches the page ONLY as pixels. It is never inserted into the DOM, so
 // document.body.innerText does not contain it and the accessibility tree cannot see it.
 var v = {nonce_js};
 var x = document.getElementById('c').getContext('2d');
 x.fillStyle = '#ffffff'; x.fillRect(0, 0, 620, 200);
 x.fillStyle = '#000000';
 x.font = 'bold 132px DejaVu Sans Mono, monospace';
 x.textBaseline = 'middle'; x.textAlign = 'center';
 x.fillText(v, 310, 108);
</script>
</body></html>"""


def make_nonce(seed: int) -> str:
	"""A deterministic nonce from `seed`, so a run is reproducible from its log.

	Deliberately not `random`: the gate records the seed, and a failure has to be
	repeatable with the identical page.
	"""
	n, out = seed, []
	for _ in range(NONCE_LEN):
		n = (n * 1103515245 + 12345) & 0x7FFFFFFF
		out.append(NONCE_ALPHABET[n % len(NONCE_ALPHABET)])
	return ''.join(out)


class Fixture:
	"""Serve the two pages on loopback for the lifetime of a `with` block.

	    with Fixture(nonce='K7RN4') as fx:
	        ...  # fx.start_url, fx.second_url
	"""

	#: Set `blank_canvas=True` for the control run: the page is byte-identical except that
	#: the canvas is left empty. G3 (an image was sent) must still pass while G4 (the model
	#: read it) must fail — which is what separates "sent" from "read".
	def __init__(self, nonce: str, *, port: int = 0, blank_canvas: bool = False) -> None:
		self.nonce = nonce
		self.blank_canvas = blank_canvas
		self.marker = 'second-page-marker'
		self._port = port
		self._server: http.server.ThreadingHTTPServer | None = None
		self._thread: threading.Thread | None = None

	@property
	def port(self) -> int:
		if self._server is None:
			raise RuntimeError('fixture is not running')
		return self._server.server_address[1]

	@property
	def origin(self) -> str:
		return f'http://127.0.0.1:{self.port}'

	@property
	def start_url(self) -> str:
		return self.origin + START_PATH

	@property
	def second_url(self) -> str:
		return self.origin + SECOND_PATH

	def start_html(self) -> str:
		return _START.format(second=SECOND_PATH)

	def second_html(self) -> str:
		nonce_js = '""' if self.blank_canvas else f'"{self.nonce}"'
		return _SECOND.format(marker=self.marker, nonce_js=nonce_js)

	def assert_nonce_not_in_dom(self) -> None:
		"""The fixture's own guarantee, checked rather than asserted in a comment.

		Strips every `<script>` block and every tag, then requires the nonce to be absent
		from what is left. If this ever fails the gate is measuring nothing: the model
		could read the nonce straight out of the serialised DOM.
		"""
		if self.blank_canvas:
			return
		html = self.second_html()
		without_scripts = re.sub(r'<script\b.*?</script>', '', html, flags=re.S | re.I)
		text = re.sub(r'<[^>]+>', ' ', without_scripts)
		if self.nonce.lower() in text.lower():
			raise AssertionError(
				f'nonce {self.nonce!r} is readable from the fixture DOM; the gate would '
				f'pass without the model ever looking at the screenshot'
			)
		for attr in ('alt', 'title', 'aria-label', 'placeholder', 'value'):
			for m in re.finditer(rf'{attr}\s*=\s*"([^"]*)"', without_scripts, re.I):
				if self.nonce.lower() in m.group(1).lower():
					raise AssertionError(f'nonce leaked into a {attr}= attribute')

	def __enter__(self) -> 'Fixture':
		self.assert_nonce_not_in_dom()
		fixture = self

		class Handler(http.server.BaseHTTPRequestHandler):
			protocol_version = 'HTTP/1.1'

			def log_message(self, *a: Any) -> None:  # noqa: A003
				pass

			def do_GET(self) -> None:  # noqa: N802
				path = self.path.split('?', 1)[0]
				if path in (START_PATH, '/'):
					payload = fixture.start_html().encode()
				elif path == SECOND_PATH:
					payload = fixture.second_html().encode()
				else:
					self.send_error(404)
					return
				self.send_response(200)
				self.send_header('Content-Type', 'text/html; charset=utf-8')
				self.send_header('Content-Length', str(len(payload)))
				# A warm cache must never be able to hide a change between control runs.
				self.send_header('Cache-Control', 'no-store, must-revalidate')
				self.end_headers()
				self.wfile.write(payload)

		self._server = http.server.ThreadingHTTPServer(('127.0.0.1', self._port), Handler)
		self._server.daemon_threads = True
		self._thread = threading.Thread(target=self._server.serve_forever,
		                                name='browsin-fixture', daemon=True)
		self._thread.start()
		# Fail here rather than inside Chrome if the socket did not come up.
		with socket.create_connection(('127.0.0.1', self.port), timeout=5):
			pass
		return self

	def __exit__(self, *exc: Any) -> None:
		if self._server is not None:
			self._server.shutdown()
			self._server.server_close()
		if self._thread is not None:
			self._thread.join(timeout=5)
