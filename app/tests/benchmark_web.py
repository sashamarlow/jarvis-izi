"""Offline latency benchmark: fresh headless Edge, synthetic messages, no account.

Run from app: python tests/benchmark_web.py --samples 40 --baseline PATH_TO_OLD_WEB_DOM_JS
Omit --baseline to measure only the current release. Results go to stdout as JSON.
"""
import argparse
import asyncio
from contextlib import AsyncExitStack
from datetime import datetime, timezone
import json
import logging
import math
from pathlib import Path
import random
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import History, Processor
from rules import load_rules
from runtime import EXAMPLE, ROOT
from web_app import DOM, dom_call, send_plus
from test_web import HTML
from playwright.async_api import async_playwright


async def old_send(page, event):
    peer = await dom_call(page, 'preflight', event['mid'], 42)
    await page.locator(f'.chat-input-main [contenteditable="true"][data-peer-id="{peer}"]:visible').fill('+', timeout=3000)
    await page.wait_for_function("() => !!document.querySelector('.chat-input-main .btn-send.send')", timeout=3000)
    before = await dom_call(page, 'commit', event['date'], 60, event['mid'], 42)
    await page.wait_for_function('ids => window.__concertWeb.acknowledged(ids)', arg=before, timeout=10000)


def summary(samples):
    ordered = sorted(samples)
    return {"samples": len(samples), "median_ms": round(statistics.median(samples), 2),
            "p95_ms": round(ordered[math.ceil(len(ordered)*0.95)-1], 2),
            "min_ms": round(ordered[0], 2), "max_ms": round(ordered[-1], 2)}


async def trial(context, script, old, delay, folder, matcher, index):
    page = await context.new_page()
    history = History(folder / f'{"old" if old else "event"}-{index}.sqlite3')
    try:
        await page.set_content(HTML)
        await page.evaluate(script)
        baseline = await dom_call(page, 'arm', -42, 42)
        processor = Processor(history, -42, baseline['baseline'],
                              datetime.fromtimestamp(baseline['since'], timezone.utc),
                              live=True, sender_ids=(42,), matcher=matcher)
        await page.evaluate("""v => {
          document.querySelector('button').addEventListener('click', () => {
            window.clickedAt = performance.now();
          }, {capture: true});
          setTimeout(() => {
            const b = document.createElement('div');
            b.className = 'bubble is-in'; b.dataset.mid = '101';
            b.dataset.peerId = '-42'; b.dataset.timestamp = String(v.date);
            const name = document.createElement('div'); name.className = 'name colored-name';
            name.dataset.peerId = '42'; name.textContent = 'Synthetic author';
            const body = document.createElement('div'); body.className = 'message'; body.textContent = v.text;
            b.append(name, body);
            window.insertedAt = performance.now();
            document.querySelector('.bubbles-inner').append(b);
          }, v.delay);
        }""", {'date': baseline['since'], 'text': EXAMPLE, 'delay': delay})
        while True:
            events = await dom_call(page, 'poll' if old else 'nextEvents', *(() if old else (1000,)))
            if events:
                break
            if old:
                await asyncio.sleep(0.1)
        event, = events
        status = await processor.handle(-42, event['mid'], event['text'],
            datetime.fromtimestamp(event['date'], timezone.utc), event['sender_id'], False,
            (lambda: old_send(page, event)) if old else
            (lambda: send_plus(page, event['date'], message_id=event['mid'], author_id=42)))
        assert status == 'sent', status
        result = await page.evaluate('({ms: clickedAt - insertedAt, clicks})')
        assert result['clicks'] == 1
        return result['ms']
    finally:
        history.close()
        await page.close()


async def main(args):
    logging.disable(logging.CRITICAL)
    matcher = load_rules(ROOT / 'rules.json')
    t0 = time.perf_counter()
    for _ in range(5000):
        assert matcher(EXAMPLE).accepted
    matcher_ms = (time.perf_counter() - t0) * 1000 / 5000
    old_dom = args.baseline.read_text(encoding='utf-8') if args.baseline else None
    values = {'event': [], 'baseline_100ms': []}
    rng = random.Random(42)
    async with AsyncExitStack() as stack:
        pw = await stack.enter_async_context(async_playwright())
        temp = stack.enter_context(tempfile.TemporaryDirectory(prefix='jarvis-offline-benchmark-'))
        browser = await pw.chromium.launch(channel='msedge', headless=True)
        context = await browser.new_context()
        await context.route('**/*', lambda route: route.abort())
        try:
            for index in range(args.samples + 3):
                delay = rng.uniform(1, 100)
                versions = [('event', DOM, False)]
                if old_dom:
                    versions.append(('baseline_100ms', old_dom, True))
                if index % 2:
                    versions.reverse()
                for name, script, old in versions:
                    ms = await asyncio.wait_for(trial(context, script, old, delay, Path(temp), matcher, index), 15)
                    if index >= 3:
                        values[name].append(ms)
        finally:
            await browser.close()
    print(json.dumps({
        'measurement': 'Synthetic DOM insertion to send button click, includes matching and durable SQLite reservation',
        'limitations': 'Headless offline Edge; no Telegram network, VPN, or delivery to other participants; 3 warmups discarded',
        'matcher_mean_ms': round(matcher_ms, 4),
        **{key: summary(value) for key, value in values.items() if value},
    }, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=int, default=40)
    parser.add_argument('--baseline', type=Path)
    args = parser.parse_args()
    if not 5 <= args.samples <= 500:
        parser.error('--samples must be between 5 and 500')
    asyncio.run(main(args))
