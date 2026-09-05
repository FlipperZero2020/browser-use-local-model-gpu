#!/usr/bin/env python3
"""Prove `phase5_gate.grade()` can fail, without a card, a browser or a lease.

    venv/bin/python tools/test_phase5_grade_offline.py

Phases 0, 2 and 4 produced five false passes between them (§10), every one of which looked
green. The rule this repo settled on is not "remember to be careful", it is **run the
control** — so every branch of the scorer is exercised here against a hand-built history,
including the two that matter most:

* a wrong answer the model never had must score FAIL with `had_then_lost` **False**, and
* a wrong answer the model *did* have in `memory` and then dropped must score FAIL with
  `had_then_lost` **True**.

If those two ever collapse into each other, the gate stops distinguishing "never read the
page" from "read it and threw the answer away", which is the one distinction the 2026-09-05
Hacker News failure turned on.
"""
from __future__ import annotations

import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / 'tools'))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location('phase5_gate', REPO / 'tools' / 'phase5_gate.py')
phase5 = importlib.util.module_from_spec(_spec)
sys.modules['phase5_gate'] = phase5
_saved_argv, sys.argv = sys.argv, ['phase5_gate']
_spec.loader.exec_module(phase5)
sys.argv = _saved_argv


class FakeAction:
	def __init__(self, name: str):
		self._name = name

	def model_dump_json(self, **_):
		return json.dumps({self._name: {'index': 1}})


class FakeOutput:
	def __init__(self, memory: str, actions: list[str]):
		self.memory = memory
		self.action = [FakeAction(a) for a in actions]


class FakeStep:
	def __init__(self, memory: str, actions: list[str]):
		self.model_output = FakeOutput(memory, actions)


class FakeHistory:
	def __init__(self, steps, final, done=True):
		self.history = steps
		self._final = final
		self._done = done

	def final_result(self):
		return self._final

	def is_done(self):
		return self._done


READ_TASK = phase5.Task(name='t', url='u', prompt='p', expect=lambda: ['Widget X'],
                        read_only=True)
ABSENT_TASK = phase5.Task(name='a', url='u', prompt='p', expect=lambda: [], absent=True,
                          forbid=['In the news'], read_only=True)

CHECKS: list[tuple[str, bool]] = []


def check(label: str, ok: bool, detail: str = ''):
	CHECKS.append((label, ok))
	print(f'  [{"PASS" if ok else "FAIL"}] {label}{("  " + detail) if detail else ""}')


def main() -> int:
	print('== the scorer accepts a genuinely correct run')
	r = phase5.grade(READ_TASK, ['Widget X'], FakeHistory(
		[FakeStep('the answer is Widget X', ['done'])],
		'The answer is Widget X.'))
	check('a correct final answer scores correct', r['correct'] is True)
	check('a correct run is never flagged had_then_lost', r['had_then_lost'] is False)

	print('\n== the scorer rejects a wrong run the model never got right')
	r = phase5.grade(READ_TASK, ['Widget X'], FakeHistory(
		[FakeStep('I think it is Widget Q', ['done'])],
		'The answer is Widget Q.'))
	check('a wrong final answer scores incorrect', r['correct'] is False)
	check('never-had-it is NOT reported as had_then_lost', r['had_then_lost'] is False,
	      '(this is the branch that separates a reading failure from an overwrite)')

	print('\n== the detector this gate exists for: had it in memory, dropped it')
	r = phase5.grade(READ_TASK, ['Widget X'], FakeHistory(
		[FakeStep('the top item is Widget X', ['input']),
		 FakeStep('the top item is Widget Q', ['done'])],
		'The answer is Widget Q.'))
	check('a dropped-correct-answer run scores incorrect', r['correct'] is False)
	check('a dropped-correct-answer run IS flagged had_then_lost', r['had_then_lost'] is True,
	      '(the 2026-09-05 Hacker News signature)')

	print('\n== the agent\'s own done flag is never consulted')
	r = phase5.grade(READ_TASK, ['Widget X'], FakeHistory(
		[FakeStep('nope', ['done'])], 'The answer is Widget Q.', done=True))
	check('done=True does not make a wrong answer pass', r['correct'] is False,
	      '(§5: never the agent\'s own done action)')

	print('\n== wasted actions are counted only where the task forbade interaction')
	r = phase5.grade(READ_TASK, ['Widget X'], FakeHistory(
		[FakeStep('m', ['input']), FakeStep('m', ['click']),
		 FakeStep('m', ['scroll']), FakeStep('m', ['done'])],
		'Widget X'))
	check('input+click counted, scroll and done are not', r['wasted_actions'] == 2,
	      f'got {r["wasted_actions"]}')
	free = phase5.Task(name='f', url='u', prompt='p', expect=lambda: ['Widget X'])
	r = phase5.grade(free, ['Widget X'], FakeHistory(
		[FakeStep('m', ['input']), FakeStep('m', ['done'])], 'Widget X'))
	check('a task that allows interaction counts no waste', r['wasted_actions'] == 0)

	print('\n== the absence task is graded on admitting absence, not on finding something')
	r = phase5.grade(ABSENT_TASK, [], FakeHistory(
		[FakeStep('m', ['done'])], 'That section does not exist on this page.'))
	check('admitting absence scores correct', r['correct'] is True)
	r = phase5.grade(ABSENT_TASK, [], FakeHistory(
		[FakeStep('m', ['done'])], "Here is the In the news box content instead."))
	check('substituting a real section scores incorrect', r['correct'] is False)
	check('the substituted section is named in the result', r['substituted'] == ['In the news'],
	      f'got {r["substituted"]}')
	r = phase5.grade(ABSENT_TASK, [], FakeHistory(
		[FakeStep('m', ['done'])], 'The weather forecast is sunny with light winds.'))
	check('inventing content instead of admitting absence scores incorrect',
	      r['correct'] is False)

	ok = sum(1 for _, v in CHECKS if v)
	print(f'\nGRADER CONTROL: {"PASSED" if ok == len(CHECKS) else "FAILED"} — '
	      f'{ok} of {len(CHECKS)} checks')
	return 0 if ok == len(CHECKS) else 1


if __name__ == '__main__':
	sys.exit(main())
