"""Offline browser tests against a synthetic DOM, never a Telegram account."""
import asyncio
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from runtime import EXAMPLE
from core import History, Processor
from web_app import DOM, dom_call, send_plus
import web_app

try:
    from playwright.async_api import async_playwright
except ImportError:
    async_playwright = None


HTML = """<html><body><div class="chat">
<div class="topbar"><span class="user-title">Offline test group</span></div>
<div class="bubbles scrolled-down"><div class="bubbles-inner">
<div class="bubble is-in" data-mid="100" data-peer-id="-42" data-timestamp="1">
<div class="message">old message</div></div></div></div>
<div class="chat-input-main"><div contenteditable="true" data-peer-id="-42"></div>
<button class="btn-send record">Send</button></div></div>
<script>
window.clicks = 0;
const field = document.querySelector('[contenteditable]');
const button = document.querySelector('button');
field.addEventListener('input', () => {
 button.className = 'btn-send ' + (field.textContent ? 'send' : 'record');
});
button.addEventListener('click', () => {
 window.clicks++;
 const b = document.createElement('div');
 b.className = 'bubble is-out'; b.dataset.mid = String(10000 + clicks);
 b.dataset.peerId = '-42'; b.dataset.timestamp = String(Math.floor(Date.now()/1000));
 const m = document.createElement('div'); m.className = 'message'; m.textContent = field.textContent;
 b.append(m); document.querySelector('.bubbles-inner').append(b);
 field.textContent = ''; field.dispatchEvent(new Event('input', {bubbles:true}));
});
</script></body></html>"""


@unittest.skipIf(async_playwright is None, "Optional: install requirements.txt to test Web mode")
class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pw = await async_playwright().start()
        try:
            self.browser = await self.pw.chromium.launch(channel="msedge", headless=True)
        except Exception:
            await self.pw.stop()
            raise
        self.page = await self.browser.new_page()
        await self.page.set_content(HTML)
        await self.page.evaluate(DOM)
        self.arm = await self.page.evaluate("() => window.__concertWeb.arm(-42, 42)")

    async def asyncTearDown(self):
        await self.browser.close()
        await self.pw.stop()

    async def add(self, mid=101, body=EXAMPLE, outgoing=False, date=None, peer=-42,
                  sender_id=42, group_sender=None, forward_from=None, reply_from=None):
        await self.page.evaluate("""v => {
          const b = document.createElement('div');
          b.className = 'bubble ' + (v.outgoing ? 'is-out' : 'is-in');
          b.dataset.mid = v.mid; b.dataset.peerId = v.peer;
          b.dataset.timestamp = v.date;
          if(v.sender_id !== null && v.forward_from === null) {
            const name = document.createElement('div'); name.className = 'name colored-name';
            name.dataset.peerId = String(v.sender_id); name.textContent = 'Same display name';
            b.append(name);
          }
          if(v.forward_from !== null) {
            const name = document.createElement('div'); name.className = 'name is-forward';
            const title = document.createElement('span'); title.className = 'peer-title';
            title.dataset.peerId = String(v.forward_from); title.textContent = 'Original author';
            name.append(title); b.append(name);
          }
          if(v.reply_from !== null) {
            const reply = document.createElement('div'); reply.className = 'reply-wrapper';
            reply.innerHTML = '<div class="name colored-name" data-peer-id="' + v.reply_from + '">Quoted author</div>';
            b.append(reply);
          }
          const m = document.createElement('div'); m.className = 'message';
          // Preserve real Telegram-style line breaks and remove metadata in the reader.
          v.body.split('\\n').forEach((line, i) => {
            if(i) m.append(document.createElement('br')); m.append(document.createTextNode(line));
          });
          const t = document.createElement('span'); t.className = 'time'; t.textContent = '12:30';
          m.append(t); b.append(m);
          if(v.group_sender !== null) {
            const group = document.createElement('div'); group.className = 'bubbles-group';
            const avatarContainer = document.createElement('div'); avatarContainer.className = 'bubbles-group-avatar-container';
            const avatar = document.createElement('div'); avatar.className = 'bubbles-group-avatar user-avatar';
            avatar.dataset.peerId = String(v.group_sender); avatarContainer.append(avatar);
            group.append(avatarContainer, b); document.querySelector('.bubbles-inner').append(group);
          } else {
            document.querySelector('.bubbles-inner').append(b);
          }
        }""", {"mid": str(mid), "body": body, "outgoing": outgoing, "peer": str(peer),
              "date": str(date if date is not None else self.arm["since"]),
              "sender_id": sender_id, "group_sender": group_sender,
              "forward_from": forward_from, "reply_from": reply_from})

    async def eligible(self):
        await self.add()
        event, = await self.poll()
        return event

    async def send(self):
        await send_plus(self.page, self.arm['since'], message_id=101, author_id=42)

    async def poll(self):
        initial = await self.page.evaluate("() => window.__concertWeb.poll()")
        await asyncio.sleep(0.06)
        return initial + await self.page.evaluate("() => window.__concertWeb.poll()")

    async def test_fresh_once_and_metadata_stripped(self):
        await self.add()
        events = await self.poll()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["text"], EXAMPLE)
        self.assertEqual(await self.poll(), [])

    async def test_old_own_wrong_chat_and_existing_edit(self):
        await self.add(99)
        await self.add(101, outgoing=True)
        await self.add(102, date=self.arm["since"]-1)
        await self.add(103, peer=-43)
        await self.page.evaluate("v => document.querySelector('.bubble[data-mid=\"100\"] .message').textContent = v", EXAMPLE)
        self.assertEqual(await self.poll(), [])

    async def test_empty_bubble_later_rendered(self):
        await self.add(body="")
        self.assertEqual(await self.poll(), [])
        await self.page.evaluate("v => document.querySelector('.bubble[data-mid=\"101\"] .message').textContent = v", EXAMPLE)
        self.assertEqual(len(await self.poll()), 1)

    async def test_chat_switch_stops(self):
        await self.page.locator('[contenteditable]').evaluate("e => e.dataset.peerId = '-43'")
        with self.assertRaises(Exception):
            await self.poll()
        self.assertEqual(await self.page.evaluate("clicks"), 0)

    async def test_arm_requires_latest_messages(self):
        await self.page.locator('.bubbles').evaluate("e => e.classList.remove('scrolled-down')")
        with self.assertRaises(Exception):
            await self.page.evaluate("() => window.__concertWeb.arm(-42, 42)")

    async def test_draft_preserved(self):
        await self.eligible()
        await self.page.locator('[contenteditable]').fill("мой черновик")
        with self.assertRaises(Exception):
            await self.send()
        self.assertEqual(await self.page.locator('[contenteditable]').inner_text(), "мой черновик")
        self.assertEqual(await self.page.evaluate("clicks"), 0)

    async def test_reply_and_channel_sender_blocked(self):
        await self.eligible()
        await self.page.locator('.chat').evaluate("e => e.classList.add('is-helper-active')")
        with self.assertRaises(Exception):
            await self.send()
        await self.page.locator('.chat').evaluate("""e => {
          e.classList.remove('is-helper-active');
          e.insertAdjacentHTML('beforeend', '<div class="new-message-send-as-container"><div class="new-message-send-as-avatar" data-peer-id="-55"></div></div>');
        }""")
        with self.assertRaises(Exception):
            await self.send()
        self.assertEqual(await self.page.evaluate("clicks"), 0)

    async def test_commit_rechecks_destination(self):
        await self.eligible()
        await self.page.locator('[contenteditable]').fill('+')
        await self.page.locator('[contenteditable]').evaluate("e => e.dataset.peerId = '-43'")
        with self.assertRaises(Exception):
            await self.page.evaluate("() => window.__concertWeb.commit(null, 60, 101, 42)")
        self.assertEqual(await self.page.evaluate("clicks"), 0)

    async def test_dry_and_live_end_to_end_deduplicated(self):
        await self.add()
        event, = await self.poll()
        with tempfile.TemporaryDirectory() as folder:
            history = History(Path(folder) / 'history.sqlite3')
            try:
                processor = Processor(history, -42, self.arm['baseline'],
                                      datetime.fromtimestamp(self.arm['since'], timezone.utc), sender_ids=(42,))
                async def handle():
                    return await processor.handle(-42, event['mid'], event['text'],
                        datetime.fromtimestamp(event['date'], timezone.utc), event['sender_id'], False,
                        lambda: self.send())
                self.assertEqual(await handle(), 'would_send')
                self.assertEqual(await self.page.evaluate('clicks'), 0)
                self.assertEqual(await self.page.locator('[contenteditable]').inner_text(), '')
                processor.live = True
                self.assertEqual(await handle(), 'sent')
                self.assertEqual(await handle(), 'duplicate')
                self.assertEqual(await self.page.evaluate('clicks'), 1)
                self.assertEqual(await self.page.locator('[contenteditable]').inner_text(), '')
                self.assertEqual(await self.page.locator('.is-out .message').inner_text(), '+')
            finally:
                history.close()

    async def test_stale_at_commit_never_clicked(self):
        await self.eligible()
        await self.page.locator('[contenteditable]').fill('+')
        await self.page.evaluate("() => {const now = Date.now(); Date.now = () => now + 61000;}")
        with self.assertRaises(Exception):
            await self.page.evaluate("date => window.__concertWeb.commit(date, 60, 101, 42)", self.arm['since'])
        self.assertEqual(await self.page.evaluate('clicks'), 0)

    async def test_disabled_send_never_clicked(self):
        await self.eligible()
        await self.page.locator('[contenteditable]').fill('+')
        await self.page.locator('button').evaluate("e => e.classList.add('btn-disabled')")
        with self.assertRaises(Exception):
            await self.page.evaluate("() => window.__concertWeb.commit(null, 60, 101, 42)")
        self.assertEqual(await self.page.evaluate('clicks'), 0)

    async def test_unconfirmed_browser_click_is_not_repeated(self):
        await self.add()
        event, = await self.poll()
        async def call(page, method, *args):
            result = await dom_call(page, method, *args)
            if method == 'sendPrepared':
                raise TimeoutError('synthetic lost acknowledgment')
            return result
        with tempfile.TemporaryDirectory() as folder:
            history = History(Path(folder) / 'history.sqlite3')
            try:
                processor = Processor(history, -42, self.arm['baseline'],
                    datetime.fromtimestamp(self.arm['since'], timezone.utc), live=True, sender_ids=(42,))
                async def handle():
                    return await processor.handle(-42, event['mid'], event['text'],
                        datetime.fromtimestamp(event['date'], timezone.utc), event['sender_id'], False,
                        lambda: self.send())
                with patch('web_app.dom_call', side_effect=call):
                    self.assertEqual(await handle(), 'failed_or_uncertain')
                    self.assertEqual(await handle(), 'duplicate')
                self.assertEqual(await self.page.evaluate('clicks'), 1)
            finally:
                history.close()


    async def test_sender_id_is_not_chat_id_or_display_name(self):
        await self.add(sender_id=99)
        event, = await self.poll()
        self.assertEqual(event['sender_id'], 99)
        with self.assertRaises(Exception):
            await self.send()
        self.assertEqual(await self.page.evaluate('clicks'), 0)
        self.assertEqual(await self.page.locator('[contenteditable]').inner_text(), '')

    async def test_missing_sender_is_unknown(self):
        await self.add(sender_id=None)
        event, = await self.poll()
        self.assertIsNone(event['sender_id'])
        with self.assertRaises(Exception):
            await self.send()
        self.assertEqual(await self.page.evaluate('clicks'), 0)

    async def test_shared_group_avatar_handles_hidden_sender_header(self):
        await self.add(sender_id=None, group_sender=42)
        event, = await self.poll()
        self.assertEqual(event['sender_id'], 42)
        await self.send()
        self.assertEqual(await self.page.evaluate('clicks'), 1)

    async def test_consecutive_messages_use_own_group_not_neighbor(self):
        await self.add(mid=101, sender_id=42, group_sender=42)
        await self.add(mid=102, sender_id=None)
        events = await self.poll()
        self.assertEqual([e['sender_id'] for e in events], [42, None])
        with self.assertRaises(Exception):
            await send_plus(self.page, self.arm['since'], message_id=102, author_id=42)
        self.assertEqual(await self.page.evaluate('clicks'), 0)

    async def test_hidden_header_in_same_group_uses_real_sender_label(self):
        await self.add(mid=101, sender_id=42, group_sender=42)
        await self.add(mid=102, sender_id=None)
        await self.page.evaluate("""() => {
            const group = document.querySelector('.bubbles-group');
            group.querySelector('.bubbles-group-avatar-container').remove();
            group.append(document.querySelector('.bubble[data-mid="102"]'));
        }""")
        self.assertEqual([e['sender_id'] for e in await self.poll()], [42, 42])

    async def test_other_person_forwarding_selected_author_is_not_selected_author(self):
        await self.add(sender_id=None, group_sender=99, forward_from=42)
        event, = await self.poll()
        self.assertEqual(event['sender_id'], 99)
        with self.assertRaises(Exception):
            await self.send()
        self.assertEqual(await self.page.evaluate('clicks'), 0)

    async def test_forward_title_without_sender_avatar_is_unknown(self):
        await self.add(sender_id=None, forward_from=42)
        event, = await self.poll()
        self.assertIsNone(event['sender_id'])

    async def test_reply_author_is_not_message_author(self):
        await self.add(sender_id=99, reply_from=42)
        event, = await self.poll()
        self.assertEqual(event['sender_id'], 99)
        with self.assertRaises(Exception):
            await self.send()
        self.assertEqual(await self.page.evaluate('clicks'), 0)

    async def test_anonymous_group_sender_never_matches_person(self):
        await self.add(sender_id=None, group_sender=-42, forward_from=42)
        event, = await self.poll()
        self.assertIsNone(event['sender_id'])

    async def test_conflicting_avatar_and_name_is_unknown(self):
        await self.add(sender_id=42, group_sender=99)
        event, = await self.poll()
        self.assertIsNone(event['sender_id'])

    async def test_text_content_cannot_spoof_sender_metadata(self):
        await self.add(sender_id=None)
        await self.page.evaluate("""() => {
            const fake = document.createElement('div');
            fake.className = 'name colored-name'; fake.dataset.peerId = '42';
            document.querySelector('.bubble[data-mid="101"] .message').append(fake);
        }""")
        event, = await self.poll()
        self.assertIsNone(event['sender_id'])

    async def test_author_rendering_resets_stability_wait(self):
        await self.add(sender_id=None)
        self.assertEqual(await self.page.evaluate('() => window.__concertWeb.poll()'), [])
        await self.page.evaluate("""() => {
            const name = document.createElement('div'); name.className = 'name colored-name';
            name.dataset.peerId = '42'; document.querySelector('.bubble[data-mid="101"]').prepend(name);
        }""")
        event, = await self.poll()
        self.assertEqual(event['sender_id'], 42)

    async def test_author_change_before_commit_never_clicks(self):
        await self.eligible()
        await self.page.locator('[contenteditable]').fill('+')
        await self.page.locator('.bubble[data-mid="101"] .name').evaluate("e => e.dataset.peerId = '99'")
        with self.assertRaises(Exception):
            await self.page.evaluate('() => window.__concertWeb.commit(null, 60, 101, 42)')
        self.assertEqual(await self.page.evaluate('clicks'), 0)

    async def test_edited_announcement_before_commit_never_clicks(self):
        await self.eligible()
        await self.page.locator('[contenteditable]').fill('+')
        await self.page.locator('.bubble[data-mid="101"] .message').evaluate("e => e.textContent = 'Отмена'")
        with self.assertRaises(Exception):
            await self.page.evaluate('() => window.__concertWeb.commit(null, 60, 101, 42)')
        self.assertEqual(await self.page.evaluate('clicks'), 0)

    async def test_unobserved_message_or_different_configured_author_cannot_commit(self):
        await self.eligible()
        await self.page.locator('[contenteditable]').fill('+')
        for mid, author in ((100, 42), (101, 99), (9999, 42)):
            with self.subTest(mid=mid, author=author), self.assertRaises(Exception):
                await self.page.evaluate('v => window.__concertWeb.commit(null, 60, v.mid, v.author)', {'mid': mid, 'author': author})
        self.assertEqual(await self.page.evaluate('clicks'), 0)

    async def test_dom_commit_cannot_click_twice_for_same_announcement(self):
        await self.eligible()
        await self.send()
        with self.assertRaises(Exception):
            await self.send()
        self.assertEqual(await self.page.evaluate('clicks'), 1)

    async def test_removed_source_snapshot_cannot_be_used(self):
        await self.eligible()
        await self.page.locator('.bubble[data-mid="101"]').evaluate('e => e.remove()')
        await self.poll()
        with self.assertRaises(Exception):
            await self.send()
        self.assertEqual(await self.page.evaluate('clicks'), 0)

    async def test_next_events_wakes_from_mutation_without_polling(self):
        waiting = asyncio.create_task(dom_call(self.page, 'nextEvents', 3000))
        await self.add()
        events = await asyncio.wait_for(waiting, 3)
        self.assertEqual([e['mid'] for e in events], [101])
        self.assertGreaterEqual(events[0]['detection_ms'], 40)
        self.assertEqual(await dom_call(self.page, 'nextEvents', 20), [])

    async def test_late_sender_metadata_is_not_lost_after_unknown_warning(self):
        await self.add(sender_id=None)
        self.assertIsNone((await self.poll())[0]['sender_id'])
        await self.page.evaluate("""() => {
          const name = document.createElement('div'); name.className = 'name colored-name';
          name.dataset.peerId = '42'; name.textContent = 'Late sender';
          document.querySelector('.bubble[data-mid="101"]').prepend(name);
        }""")
        event, = await dom_call(self.page, 'nextEvents', 1000)
        self.assertEqual(event['sender_id'], 42)
        await self.send()
        self.assertEqual(await self.page.evaluate('clicks'), 1)

    async def test_changes_reset_settle_timer_and_deliver_final_text_once(self):
        await self.add(body='Начало')
        await self.page.evaluate("""text => {
          setTimeout(() => document.querySelector('.bubble[data-mid="101"] .message').textContent = text, 15);
        }""", EXAMPLE)
        event, = await dom_call(self.page, 'nextEvents', 1000)
        self.assertEqual(event['text'], EXAMPLE)
        self.assertEqual(await self.poll(), [])

    async def test_delayed_button_readiness_and_acknowledgment_are_event_driven(self):
        await self.eligible()
        await self.page.evaluate("""() => {
          const old = document.querySelector('button');
          const button = old.cloneNode(true); old.replaceWith(button);
          document.querySelector('[contenteditable]').addEventListener('input', () => {
            setTimeout(() => button.className = 'btn-send send', 25);
          });
          button.addEventListener('click', () => {
            clicks++;
            setTimeout(() => {
              const bubble = document.createElement('div');
              bubble.className = 'bubble is-out'; bubble.dataset.mid = '10001';
              bubble.dataset.peerId = '-42'; bubble.dataset.timestamp = String(Math.floor(Date.now()/1000));
              bubble.innerHTML = '<div class="message">+</div>';
              document.querySelector('.bubbles-inner').append(bubble);
              document.querySelector('[contenteditable]').textContent = '';
            }, 25);
          });
        }""")
        timing = await send_plus(self.page, self.arm['since'], message_id=101, author_id=42)
        self.assertEqual(await self.page.evaluate('clicks'), 1)
        self.assertGreaterEqual(timing['click_to_ack_ms'], 20)
        self.assertGreaterEqual(timing['first_seen_to_click_ms'], timing['detection_ms'])

    async def test_pending_send_stops_on_chat_change_before_button_ready(self):
        await self.eligible()
        await self.page.evaluate("""() => {
          document.querySelector('button').replaceWith(document.querySelector('button').cloneNode(true));
          document.querySelector('[contenteditable]').addEventListener('input', e => {
            setTimeout(() => e.target.dataset.peerId = '-43', 15);
          });
        }""")
        with self.assertRaisesRegex(RuntimeError, 'изменились'):
            await self.send()
        self.assertEqual(await self.page.evaluate('clicks'), 0)

    async def test_sleep_jump_blocks_queued_message(self):
        await self.eligible()
        await self.page.evaluate('() => {const now = Date.now(); Date.now = () => now + 61000;}')
        with self.assertRaisesRegex(RuntimeError, 'пауза'):
            await dom_call(self.page, 'nextEvents', 1000)
        with self.assertRaises(Exception):
            await self.send()
        self.assertEqual(await self.page.evaluate('clicks'), 0)

    async def test_rearm_discards_pending_timers_and_previous_messages(self):
        await self.add()
        await dom_call(self.page, 'arm', -42, 42)
        self.assertEqual(await dom_call(self.page, 'nextEvents', 100), [])
        await self.add(mid=102)
        self.assertEqual([e['mid'] for e in await dom_call(self.page, 'nextEvents', 1000)], [102])

    async def test_idle_health_check_does_not_scan_message_list(self):
        await self.page.evaluate("""() => {
          window.listScans = 0;
          const original = Element.prototype.querySelectorAll;
          Element.prototype.querySelectorAll = function(selector) {
            if(selector.includes('.bubble[') || selector === '.bubble') listScans++;
            return original.call(this, selector);
          };
        }""")
        self.assertEqual(await dom_call(self.page, 'nextEvents', 1150), [])
        self.assertEqual(await self.page.evaluate('listScans'), 0)

    async def test_observer_delivers_burst_once_and_in_order(self):
        for mid in range(101, 111):
            await self.add(mid=mid)
        events = await self.poll()
        self.assertEqual([e['mid'] for e in events], list(range(101, 111)))
        self.assertEqual(await self.poll(), [])

    async def test_author_selection_distinguishes_same_names_and_forwarded_titles(self):
        await self.add(mid=101, sender_id=42)
        await self.add(mid=102, sender_id=99)
        await self.add(mid=103, sender_id=None, group_sender=99, forward_from=42)
        await self.add(mid=104, sender_id=None)
        choices = await dom_call(self.page, 'authorMessages', -42)
        self.assertEqual([e['author_id'] for e in choices], [99, 99, 42])
        self.assertNotIn('Original author', [e['name'] for e in choices])
        chosen = await dom_call(self.page, 'selectAuthor', -42, 101, 42)
        self.assertEqual(chosen['author_id'], 42)
        self.assertEqual(await self.page.evaluate('clicks'), 0)

    async def test_author_selection_rejects_changed_sender_message_and_chat(self):
        await self.add()
        await dom_call(self.page, 'authorMessages', -42)
        await self.page.locator('.bubble[data-mid="101"] .name').evaluate("e => e.dataset.peerId = '99'")
        with self.assertRaises(RuntimeError):
            await dom_call(self.page, 'selectAuthor', -42, 101, 42)
        await self.page.locator('.bubble[data-mid="101"] .name').evaluate("e => e.dataset.peerId = '42'")
        await self.page.locator('.bubble[data-mid="101"] .message').evaluate("e => e.textContent = 'Другое сообщение'")
        with self.assertRaises(RuntimeError):
            await dom_call(self.page, 'selectAuthor', -42, 101, 42)
        await self.page.locator('[contenteditable]').evaluate("e => e.dataset.peerId = '-43'")
        with self.assertRaises(RuntimeError):
            await dom_call(self.page, 'authorMessages', -42)

    async def test_removed_list_stops_waiter_without_sending(self):
        waiting = asyncio.create_task(dom_call(self.page, 'nextEvents', 3000))
        await self.page.locator('.bubbles-inner').evaluate('e => e.remove()')
        with self.assertRaisesRegex(RuntimeError, 'заменён'):
            await asyncio.wait_for(waiting, 3)
        self.assertEqual(await self.page.evaluate('clicks'), 0)

    async def test_production_monitor_receives_observer_event_and_sends_once(self):
        # The page stays about:blank. Only the monitor's URL check is adapted for
        # this synthetic page; production detection, matching, storage and send run.
        await self.page.evaluate("""() => {
          const original = window.__concertWeb.arm;
          window.__concertWeb.arm = function(...args) {
            const result = original.apply(this, args); window.monitorArmed = true; return result;
          };
        }""")
        with tempfile.TemporaryDirectory() as folder, patch.object(web_app, 'DATA', Path(folder)), \
             patch.object(web_app, 'URL', 'about:blank'):
            task = asyncio.create_task(web_app.monitor(self.page, -42, True, 42))
            try:
                await self.page.wait_for_function('() => window.monitorArmed === true')
                await self.add()
                await self.page.wait_for_function('() => clicks === 1')
                await self.page.locator('[contenteditable]').evaluate("e => e.dataset.peerId = '-43'")
                with self.assertRaisesRegex(RuntimeError, 'изменились'):
                    await asyncio.wait_for(task, 3)
                with closing(sqlite3.connect(Path(folder) / 'web-history.sqlite3')) as db:
                    self.assertEqual(db.execute('SELECT message_id,status FROM decisions').fetchall(), [(101, 'sent')])
                self.assertEqual(await self.page.evaluate('clicks'), 1)
            finally:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass


if __name__ == '__main__':
    unittest.main()
