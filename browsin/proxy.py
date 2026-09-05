"""A logging reverse proxy on `127.0.0.1:11434`, forwarding to the leased endpoint.

PLAN.md §4 asks for this because **the agent has no idea how big its own prompts are**.
`ChatOllama` reads only `message.content` and builds its `ChatInvokeUsage` from nothing —
browser-use's token service reports zeros on this path by design — so `history.usage` can
never answer "did we blow the context window". Ollama's own `/api/chat` reply carries
`prompt_eval_count`, `eval_count` and the duration breakdown, and `ChatOllama` discards all
of it. Sitting in the middle is the only place that number is observable.

**It must not change the request.** The body is forwarded as the exact bytes that arrived —
never `json.loads` → `json.dumps`, which would reorder keys, drop `tools: []` (which ollama's
client does put on the wire), and re-encode floats. Parsing happens on a *copy*, purely to
log. The method, path and query are preserved verbatim so the whole client side can point
here: `/api/ps`, `/api/tags` and `/api/show` pass through unchanged.

Three things it must never do, each of which would corrupt warden's accounting rather than
merely this run's numbers:

* **Never inject or alter `keep_alive`.** `keep_alive: 0` is warden's own eviction verb.
* **Never alter `options`, `num_ctx`, `model` or `format`.** `num_ctx` is one number written
  in two places (the client's `ollama_options`, and the window warden booked `cost_mib` at);
  a proxy that "helpfully" normalised it would put warden's book silently wrong.
* **Never be the thing that times out.** The upstream socket timeout is deliberately longer
  than the caller's `llm_timeout`, because a proxy that gives up first turns a slow
  generation into a confusing client error while the GPU keeps generating.

An aborted exchange is logged, not dropped. When `llm_timeout` fires, httpx abandons the
request mid-flight and the proxy sees a client disconnect with no response — so
`prompt_eval_count` never arrives. Logging that as `CLIENT_ABORTED` is what keeps "the gate
could not measure the prompt" distinguishable from "no request was made", which is the
difference between a failure and a false pass.

Stdlib only, matching `browsin.lease`'s dependency floor.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import socket
import struct
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

#: Ollama's own port. Binding it locally means `host='http://127.0.0.1:11434'` is a
#: drop-in for the box's address, and `assert ':11434' in host` (which guards ollama's
#: silent fall-back to port 80) keeps holding.
PORT = 11434

#: Headers that describe *this* hop and must not be relayed to the next one.
HOP_BY_HOP = frozenset({
	'connection', 'keep-alive', 'transfer-encoding', 'te', 'trailer', 'upgrade',
	'proxy-authorization', 'proxy-authenticate', 'host', 'content-length',
})

#: Longer than any `llm_timeout` this project sets. See the module docstring.
UPSTREAM_TIMEOUT_S = 1800.0


def _png_size(raw: bytes) -> tuple[int, int] | None:
	"""(width, height) from a PNG's IHDR, or None if it is not a PNG."""
	if len(raw) < 24 or raw[:8] != b'\x89PNG\r\n\x1a\n':
		return None
	return struct.unpack('>II', raw[16:24])


def _describe_request(body: bytes) -> dict[str, Any]:
	"""Everything worth logging about a request, from a parsed COPY of the bytes."""
	out: dict[str, Any] = {'body_bytes': len(body)}
	try:
		obj = json.loads(body)
	except (json.JSONDecodeError, UnicodeDecodeError):
		out['parse_error'] = True
		return out
	if not isinstance(obj, dict):
		return out
	out['model'] = obj.get('model')
	out['stream'] = obj.get('stream')
	opts = obj.get('options')
	if isinstance(opts, dict):
		out['options'] = opts
		out['num_ctx'] = opts.get('num_ctx')
	# `format` has a no-schema branch in ChatOllama (compaction takes it), so record
	# presence as a boolean rather than assuming every exchange carries one.
	fmt = obj.get('format')
	out['format_present'] = fmt is not None
	if fmt is not None:
		out['format_bytes'] = len(json.dumps(fmt))
	# `tools: []` IS on the wire — ollama's client materialises an empty list — so absent
	# and empty are different things and must not be collapsed.
	out['tools_present'] = 'tools' in obj
	out['tools_len'] = len(obj['tools']) if isinstance(obj.get('tools'), list) else None
	out['keep_alive'] = obj.get('keep_alive', '<absent>')

	msgs = obj.get('messages')
	if isinstance(msgs, list):
		out['message_count'] = len(msgs)
		roles, chars, images = [], 0, []
		for m in msgs:
			if not isinstance(m, dict):
				continue
			content = m.get('content') or ''
			roles.append({'role': m.get('role'), 'chars': len(content)})
			chars += len(content)
			for img in (m.get('images') or []):
				try:
					import base64
					raw = base64.b64decode(img)
				except Exception:
					images.append({'decode_error': True})
					continue
				images.append({
					'bytes': len(raw),
					'sha256': hashlib.sha256(raw).hexdigest(),
					'png': _png_size(raw),
				})
		out['messages'] = roles
		out['total_text_chars'] = chars
		out['image_count'] = len(images)
		out['images'] = images
	return out


def _describe_response(obj: dict) -> dict[str, Any]:
	keys = ('model', 'done', 'done_reason', 'prompt_eval_count', 'eval_count',
	        'prompt_eval_duration', 'eval_duration', 'total_duration', 'load_duration')
	return {k: obj.get(k) for k in keys if k in obj}


class _Handler(BaseHTTPRequestHandler):
	protocol_version = 'HTTP/1.1'
	server_version = 'browsin-proxy/1'

	# The default logs every request to stderr; ours go to the JSONL.
	def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
		pass

	def _relay(self) -> None:
		srv: Proxy = self.server.proxy  # type: ignore[attr-defined]
		seq = srv.next_seq()
		t0 = time.monotonic()
		length = int(self.headers.get('Content-Length') or 0)
		body = self.rfile.read(length) if length else b''

		record: dict[str, Any] = {
			'seq': seq,
			't_start': round(t0 - srv.t_origin, 3),
			'method': self.command,
			'path': self.path,
			'request': _describe_request(body) if body else {'body_bytes': 0},
		}

		headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
		headers['Host'] = srv.upstream_authority
		if body:
			headers['Content-Length'] = str(len(body))

		conn = None
		try:
			conn = http.client.HTTPConnection(srv.upstream_host, srv.upstream_port,
			                                  timeout=UPSTREAM_TIMEOUT_S)
			# The exact bytes that arrived. Never re-serialised.
			conn.request(self.command, self.path, body=body, headers=headers)
			resp = conn.getresponse()
			record['status'] = resp.status

			out_headers = [(k, v) for k, v in resp.getheaders()
			               if k.lower() not in HOP_BY_HOP]
			declared = resp.getheader('Content-Length')

			if declared is not None:
				payload = resp.read()
				self.send_response(resp.status)
				for k, v in out_headers:
					self.send_header(k, v)
				self.send_header('Content-Length', str(len(payload)))
				self.end_headers()
				self.wfile.write(payload)
				record['response_bytes'] = len(payload)
				try:
					record['response'] = _describe_response(json.loads(payload))
				except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
					pass
			else:
				# Streaming NDJSON. Forward each chunk as it arrives — buffering the whole
				# response would change latency and could trip the caller's own timeout —
				# while keeping the last complete line, which is where the counts live.
				self.send_response(resp.status)
				for k, v in out_headers:
					self.send_header(k, v)
				self.send_header('Transfer-Encoding', 'chunked')
				self.end_headers()
				total, tail = 0, b''
				while True:
					chunk = resp.read(65536)
					if not chunk:
						break
					total += len(chunk)
					tail = (tail + chunk)[-65536:]
					self.wfile.write(f'{len(chunk):X}\r\n'.encode() + chunk + b'\r\n')
				self.wfile.write(b'0\r\n\r\n')
				record['response_bytes'] = total
				for line in reversed(tail.split(b'\n')):
					if not line.strip():
						continue
					try:
						record['response'] = _describe_response(json.loads(line))
						break
					except (json.JSONDecodeError, UnicodeDecodeError):
						continue
			self.wfile.flush()
		except (BrokenPipeError, ConnectionResetError):
			# The caller went away mid-flight — this is what an `llm_timeout` looks like
			# from here. It must be logged, not dropped: a missing response is not a
			# missing request, and a gate that cannot tell them apart passes for free.
			record['status'] = 'CLIENT_ABORTED'
		except Exception as exc:  # upstream failure — record it and tell the caller
			record['status'] = 'UPSTREAM_ERROR'
			record['error'] = f'{type(exc).__name__}: {exc}'
			try:
				self.send_error(502, 'browsin-proxy: upstream failed')
			except Exception:
				pass
		finally:
			if conn is not None:
				conn.close()
			record['t_end'] = round(time.monotonic() - srv.t_origin, 3)
			record['elapsed_s'] = round(record['t_end'] - record['t_start'], 3)
			srv.write(record)

	do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _relay


class Proxy:
	"""Run the proxy for the lifetime of a `with` block.

	    with Proxy(card.endpoint, log_path) as p:
	        llm = ChatOllama(model=..., host=p.url, ...)
	"""

	def __init__(self, upstream: str, log_path: str | Path, *, port: int = PORT) -> None:
		parsed = urllib.parse.urlparse(upstream)
		if not parsed.hostname:
			raise ValueError(f'upstream must be an absolute URL, got {upstream!r}')
		self.upstream = upstream.rstrip('/')
		self.upstream_host = parsed.hostname
		self.upstream_port = parsed.port or 80
		self.upstream_authority = f'{self.upstream_host}:{self.upstream_port}'
		self.port = port
		self.log_path = Path(log_path)
		self.log_path.parent.mkdir(parents=True, exist_ok=True)
		self._seq = 0
		self._lock = threading.Lock()
		self._fh = None
		self._server: ThreadingHTTPServer | None = None
		self._thread: threading.Thread | None = None
		self.t_origin = time.monotonic()

	@property
	def url(self) -> str:
		return f'http://127.0.0.1:{self.port}'

	def next_seq(self) -> int:
		with self._lock:
			self._seq += 1
			return self._seq

	def write(self, record: dict) -> None:
		with self._lock:
			if self._fh is not None:
				self._fh.write(json.dumps(record) + '\n')
				self._fh.flush()

	def __enter__(self) -> 'Proxy':
		# An explicit pre-check, because two proxies splitting the traffic would silently
		# halve every count the gate reconciles against — and there is no SO_REUSEPORT here
		# to make that possible by accident.
		#
		# The probe MUST set SO_REUSEADDR, because `HTTPServer.allow_reuse_address` is 1 and
		# so the real server will. Without it the probe is stricter than the server: measured
		# 2026-09-04, a run started seconds after the previous one failed with "Address
		# already in use" against a socket in TIME_WAIT with nothing listening at all. The
		# guard must refuse a live listener, not a closing connection.
		probe = socket.socket()
		probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
		try:
			probe.bind(('127.0.0.1', self.port))
		except OSError as exc:
			raise RuntimeError(
				f'127.0.0.1:{self.port} is already bound ({exc}); refusing to start a second '
				f'proxy — the request counts would be split between them and every '
				f'reconciliation in the gate would be wrong.'
			) from exc
		finally:
			probe.close()

		self._fh = self.log_path.open('a')
		self.t_origin = time.monotonic()
		self._server = ThreadingHTTPServer(('127.0.0.1', self.port), _Handler)
		self._server.daemon_threads = True
		self._server.proxy = self  # type: ignore[attr-defined]
		self._thread = threading.Thread(target=self._server.serve_forever,
		                                name='browsin-proxy', daemon=True)
		self._thread.start()
		return self

	def __exit__(self, *exc: Any) -> None:
		if self._server is not None:
			self._server.shutdown()
			self._server.server_close()
		if self._thread is not None:
			self._thread.join(timeout=5)
		if self._fh is not None:
			self._fh.close()
			self._fh = None

	# ── reading the log back ───────────────────────────────────────────────────────────

	def records(self) -> list[dict]:
		if not self.log_path.exists():
			return []
		return [json.loads(ln) for ln in self.log_path.read_text().splitlines() if ln.strip()]

	def chat_records(self) -> list[dict]:
		return [r for r in self.records() if r.get('path', '').startswith('/api/chat')]

	def first_vision_request(self) -> dict | None:
		"""The lowest-seq `/api/chat` that actually carried an image.

		The pairing is the whole point: a prompt-size number and the proof that a
		screenshot was attached have to come from the *same* request, or the measurement
		rewards precisely the broken run it exists to catch.
		"""
		with_images = [r for r in self.chat_records()
		               if (r.get('request', {}).get('image_count') or 0) > 0]
		return min(with_images, key=lambda r: r['seq']) if with_images else None
