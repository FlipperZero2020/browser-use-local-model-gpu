#!/usr/bin/env python3
"""Why does every `done` fail schema validation? Capture the raw bytes the model emits.

Phase 4's first full run drove the browser correctly and then could not finish: every
attempt to emit the terminating `done` action came back as

    15 validation errors for ActionModelUnion
    DoneActionModel
      Input should be a valid dictionary or instance of DoneActionModel
      [type=model_type, input_value=PydanticUndefined, input_type=PydanticUndefinedType]

`PydanticUndefined` for *all fifteen* union members at once is the signature of a
**structurally** wrong action object, not of a model that picked the wrong action — so this
is worth one lease to see the actual string rather than guessing.

browser-use cannot answer it: `save_conversation_path` is gated on `last_model_output`
(agent/service.py:1720), so no prompt is dumped for exactly the steps that failed, and
`ChatOllama` throws the raw completion away when `model_validate_json` raises
(llm/ollama/chat.py:94). The only way to see it is to make the same request and print it.
"""
from __future__ import annotations

import os
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRATCH = REPO / 'runs' / 'diag-tmp'
(SCRATCH / 'downloads').mkdir(parents=True, exist_ok=True)
os.environ['TMPDIR'] = str(SCRATCH)
tempfile.tempdir = str(SCRATCH)
os.environ['BROWSER_USE_CONFIG_DIR'] = str(REPO / 'runs' / 'diag-config')
sys.path.insert(0, str(REPO))

import asyncio  # noqa: E402
import json  # noqa: E402

import browsin  # noqa: E402,F401
from browsin.agent import build_agent, build_llm, build_tools  # noqa: E402
from browsin.lease import hold  # noqa: E402

WORKLOAD = 'ollama:qwen2.5vl-32k:7b'
MODEL_TAG = 'qwen2.5vl-32k:7b'
NUM_CTX = 32768


async def main() -> int:
	async with hold(WORKLOAD, reason='diag_done', num_ctx=NUM_CTX, ttl_s=180) as card:
		print(f'lease ok, served num_ctx={card.num_ctx}', flush=True)
		llm = build_llm(host=card.endpoint, model=MODEL_TAG, num_ctx=NUM_CTX)

		# Build the very same output schema the agent uses. Constructing the Agent is the
		# only honest way to get it: `_update_action_models_for_page` rebuilds AgentOutput
		# from the live registry on every step, so a hand-made schema would be a different
		# question.
		agent = build_agent(task='diagnostic', llm=llm, browser_session=None,
		                    tools=build_tools())
		schema = agent.AgentOutput.model_json_schema()
		print(f'AgentOutput schema: {len(json.dumps(schema))} bytes, '
		      f'$defs={len(schema.get("$defs", {}))}', flush=True)
		print('action property:', json.dumps(schema['properties'].get('action'))[:600],
		      flush=True)

		from browser_use.llm.messages import SystemMessage, UserMessage

		messages = [
			SystemMessage(content=(
				'You control a browser. Reply ONLY with the JSON structure you are given. '
				'The task is finished, so emit the done action.')),
			UserMessage(content=(
				'The task is complete. The access code you read was XKRAV. '
				'Emit exactly one action: done, with success true and the text '
				'"The access code is XKRAV".')),
		]

		# 1. What does the model actually produce for the real schema?
		try:
			out = await llm.ainvoke(messages, output_format=agent.AgentOutput)
			print('\nVALIDATED OK:', out.completion, flush=True)
		except Exception as exc:
			print(f'\nvalidation failed as expected: {type(exc).__name__}', flush=True)

		# 2. The same request with NO output_format, so nothing parses it and we see the
		#    literal bytes. ChatOllama takes its no-format branch here.
		raw = await llm.ainvoke(messages)
		print('\n--- RAW COMPLETION (no output_format) ---', flush=True)
		print(raw.completion[:3000], flush=True)

		# 3. The same request WITH format=, driven straight at ollama so the response is
		#    ours to read rather than ChatOllama's to discard.
		import urllib.request
		payload = {
			'model': MODEL_TAG,
			'messages': [{'role': m.role, 'content': m.text} for m in messages],
			'format': schema,
			'stream': False,
			'options': {'num_ctx': NUM_CTX},
		}
		req = urllib.request.Request(
			f'{card.endpoint}/api/chat',
			data=json.dumps(payload).encode(),
			headers={'Content-Type': 'application/json'})
		with urllib.request.urlopen(req, timeout=300) as r:
			body = json.loads(r.read())
		content = body.get('message', {}).get('content', '')
		print('\n--- GRAMMAR-CONSTRAINED COMPLETION (format=AgentOutput) ---', flush=True)
		print(content[:3000], flush=True)
		print(f'\nprompt_eval_count={body.get("prompt_eval_count")} '
		      f'done_reason={body.get("done_reason")}', flush=True)

		try:
			parsed = json.loads(content)
			print('\nparsed JSON keys:', list(parsed))
			print('action value:', json.dumps(parsed.get('action'))[:800])
		except json.JSONDecodeError as exc:
			print('not valid JSON:', exc)
			return 1

		try:
			agent.AgentOutput.model_validate(parsed)
			print('\n=> pydantic ACCEPTS it')
		except Exception as exc:
			print(f'\n=> pydantic REJECTS it: {type(exc).__name__}')
			print(str(exc)[:1500])
	return 0


if __name__ == '__main__':
	code = asyncio.run(main())
	sys.stdout.flush()
	os._exit(code)
