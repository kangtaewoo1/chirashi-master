"""Chrome slot and lifetime invariants for workroom publishing, without Flask or a real browser.

2026-11-23 incident: publish_max_chromes was 10 but 34 PUB Chromes were alive (swap 4GB full,
295 Chrome OOM kills in 36h). The slot was handed back before the Chrome was closed, and quit()
or any Selenium command could block forever, so stuck Chromes piled up outside the limit.
"""
import ast
import os
import subprocess
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

APP = Path(__file__).with_name('app.py')


def load(names, env):
    tree = ast.parse(APP.read_text(encoding='utf-8'))
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {f.name for f in fns} == set(names), names
    exec(compile(ast.Module(body=fns, type_ignores=[]), 'app.py', 'exec'), env)
    return env


class FakeProc:
    pid = None

    def __init__(self):
        self.killed = 0

    def kill(self):
        self.killed += 1

    def wait(self, timeout=None):
        return 0


class FakeDriver:
    def __init__(self, quit_secs=0.0):
        self.service = SimpleNamespace(process=FakeProc())
        self.quit_secs = quit_secs
        self.quit_called = threading.Event()

    def quit(self):
        self.quit_called.set()
        if self.quit_secs:
            time.sleep(self.quit_secs)


class PublishSlotTest(unittest.TestCase):
    """The Chrome must be closed before the global Chrome slot is handed back."""

    def run_publish(self, results):
        """Returns (events, escaped_exception). app.py swallows exceptions from release(), so a
        violation is recorded as an event instead of asserted inside release()."""
        drivers = {}
        events = []
        site = {'id': 's1', 'site_url': 'https://example.com', 'bo_table': 'free', 'name': 'ex'}
        site_lock = threading.Lock()

        class Slot:
            def acquire(self):
                events.append('acquire')

            def release(self):
                open_chrome = threading.current_thread().name in drivers
                events.append('RELEASED-WITH-CHROME-OPEN' if open_chrome else 'release')

        outcomes = iter(results)

        def do_post(site, title, html):
            drivers[threading.current_thread().name] = object()
            events.append('chrome')
            outcome = next(outcomes)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        def reset_driver():
            if drivers.pop(threading.current_thread().name, None) is not None:
                events.append('closed')

        env = {
            'load_sites': lambda: [site], 'is_publishable': lambda s: True,
            '_site_lock': lambda sid: site_lock, '_manual_queue_pending': lambda sid: False,
            'under_daily_limit': lambda *a: True, 'under_min_interval': lambda *a: (True, 0),
            '_fix_kw_dong': lambda kw: kw, '_auto_subkeywords': lambda m: {},
            '_global_gen_sem': threading.Lock,
            'generate_article': lambda *a, **k: ('<p>body</p>', 'title'),
            'secrets': __import__('secrets'), 'time': SimpleNamespace(time=time.time, sleep=lambda s: None),
            'datetime': datetime, 'threading': threading, '_RELLINK_TLS': threading.local(),
            'history_add': lambda rec: None,
            'history_update': lambda *a, **k: events.append('history'),
            'do_post': do_post, 'reset_driver': reset_driver,
            'classify_fail': lambda m: ('x', 'fail', True), 'login_requirement_confirmed': lambda m: False,
            'set_site_flag': lambda *a, **k: None,
            'finalize_post': lambda *a, **k: events.append('finalize'),
            '_post_password': lambda *a: 'pw', '_post_read_block_reason': lambda u: '',
            '_LAST_POST_QUALITY': {}, 'STATS_LOCK': threading.Lock(),
            'wk_stats': {'success': 0, 'fail': 0, 'done': 0}, 'add_log': lambda *a, **k: None,
            '_kst_now': datetime.now,
        }
        load(['_publish_combo_to_site'], env)
        escaped = None
        try:
            env['_publish_combo_to_site'](site, {}, 'room', 'rid', {'publish_loop_enabled': True},
                                          chrome_sem=Slot())
        except BaseException as exc:   # the escaping-exception test raises a BaseException on purpose
            escaped = exc
        self.assertFalse(site_lock.locked(), 'site lock leaked')
        self.assertEqual(drivers, {}, 'Chrome leaked after publish')
        self.assertNotIn('RELEASED-WITH-CHROME-OPEN', events)
        self.assertEqual(events.count('acquire'), 1)
        self.assertEqual(events.count('release'), 1, 'slot must be released exactly once')
        return events, escaped

    def test_success_closes_chrome_before_slot_and_post_processing(self):
        url = 'https://example.com/bbs/board.php?bo_table=free&wr_id=7'
        events, escaped = self.run_publish([(True, url)])
        self.assertIsNone(escaped)
        self.assertLess(events.index('closed'), events.index('release'))
        self.assertLess(events.index('release'), events.index('finalize'))

    def test_failures_and_exceptions_never_release_with_chrome_open(self):
        events, escaped = self.run_publish(
            [RuntimeError('tab crashed'), (False, 'no form'), (False, 'no form')])
        self.assertIsNone(escaped)
        self.assertEqual(events.count('chrome'), 3)
        self.assertEqual(events.count('closed'), 3)
        self.assertLess(events.index('release'), events.index('finalize'))

    def test_escaping_exception_still_closes_chrome_before_slot(self):
        class Abort(BaseException):
            pass
        events, escaped = self.run_publish([Abort()])
        self.assertIsInstance(escaped, Abort)
        self.assertLess(events.index('closed'), events.index('release'))
        self.assertNotIn('finalize', events)


class BoundedQuitTest(unittest.TestCase):
    def env(self, quit_timeout):
        env = {'threading': threading, 'secrets': __import__('secrets'), 'os': os,
               '_QUIT_TIMEOUT_S': quit_timeout, '_proc_children': lambda pid: [],
               '_kill_pid': lambda *a: True}
        load(['reset_driver', '_quit_bounded', '_kill_driver_tree'], env)
        env['_drv_lock'] = threading.Lock()
        return env

    def test_hung_quit_returns_within_bound_and_process_is_killed(self):
        env = self.env(0.3)
        driver = FakeDriver(quit_secs=5)
        env['_drivers'] = {threading.current_thread().name: driver}
        started = time.time()
        env['reset_driver']()
        self.assertLess(time.time() - started, 2.0)
        self.assertTrue(driver.quit_called.is_set())
        self.assertEqual(driver.service.process.killed, 1)
        self.assertEqual(env['_drivers'], {})

    def test_normal_quit_does_not_wait_for_timeout(self):
        env = self.env(5)
        driver = FakeDriver()
        env['_drivers'] = {threading.current_thread().name: driver}
        started = time.time()
        env['reset_driver']()
        self.assertLess(time.time() - started, 1.0)
        self.assertEqual(driver.service.process.killed, 1)


class ReaperTest(unittest.TestCase):
    def test_overage_reaper_takes_only_old_pub_chromes(self):
        logs, killed = [], []
        env = {'time': time, 'load_config': lambda: {'pub_chrome_max_age_min': 20},
               'add_log': lambda *a, **k: logs.append(a)}
        load(['_reap_overage_pub_drivers'], env)
        env['_kill_driver_tree'] = killed.append
        env['_drv_lock'] = threading.Lock()
        now = time.time()
        old_pub, young_pub, old_signup, legacy, remote = (FakeDriver() for _ in range(5))
        old_pub._born = now - 25 * 60
        young_pub._born = now - 5 * 60
        old_signup._born = now - 120 * 60
        remote._born = now - 120 * 60
        env['_drivers'] = {'PUB-aaaaaa-0001': old_pub, 'PUB-bbbbbb-0002': young_pub,
                           'SIGNUP-1': old_signup, 'PUB-cccccc-0003': legacy,
                           '__SBR__PUB-dddddd-0004': remote}
        self.assertEqual(env['_reap_overage_pub_drivers'](), 1)
        self.assertEqual(killed, [old_pub])
        self.assertEqual(set(env['_drivers']), {'PUB-bbbbbb-0002', 'SIGNUP-1', 'PUB-cccccc-0003',
                                                '__SBR__PUB-dddddd-0004'})
        self.assertEqual(len(logs), 1)

    def test_max_age_has_a_floor(self):
        killed = []
        env = {'time': time, 'load_config': lambda: {'pub_chrome_max_age_min': 1},
               'add_log': lambda *a, **k: None}
        load(['_reap_overage_pub_drivers'], env)
        env['_kill_driver_tree'] = killed.append
        env['_drv_lock'] = threading.Lock()
        driver = FakeDriver()
        driver._born = time.time() - 3 * 60   # older than 1 min, younger than the 5 min floor
        env['_drivers'] = {'PUB-eeeeee-0005': driver}
        self.assertEqual(env['_reap_overage_pub_drivers'](), 0)
        self.assertEqual(killed, [])

    def test_dead_thread_reaper_never_blocks_on_quit(self):
        killed = []
        env = {'threading': threading}
        load(['_reap_dead_thread_drivers'], env)
        env['_kill_driver_tree'] = killed.append
        env['_drv_lock'] = threading.Lock()
        hung = FakeDriver(quit_secs=30)
        mine = FakeDriver()
        env['_drivers'] = {'PUB-dead00-0001': hung, threading.current_thread().name: mine}
        started = time.time()
        self.assertEqual(env['_reap_dead_thread_drivers'](), 1)
        self.assertLess(time.time() - started, 1.0)
        self.assertEqual(killed, [hung])
        self.assertFalse(hung.quit_called.is_set())
        self.assertIn(threading.current_thread().name, env['_drivers'])


@unittest.skipUnless(os.name == 'posix' and os.path.isdir('/proc'), 'needs Linux /proc')
class RealProcessTreeTest(unittest.TestCase):
    """chromedriver -> chrome -> renderer, played by sh -> sh -> sleep."""

    def test_kill_driver_tree_kills_children_and_grandchildren(self):
        env = {'os': os}
        load(['_kill_driver_tree', '_proc_children', '_proc_stat', '_kill_pid'], env)
        parent = subprocess.Popen(['sh', '-c', "sh -c 'sleep 300 & wait' & wait"])
        try:
            deadline = time.time() + 5
            child = grandchild = None
            while time.time() < deadline and not grandchild:
                kids = env['_proc_children'](parent.pid)
                child = kids[0] if kids else None
                grand = env['_proc_children'](child) if child else []
                grandchild = grand[0] if grand else None
                time.sleep(0.05)
            self.assertTrue(child and grandchild, 'process tree did not start')
            env['_kill_driver_tree'](SimpleNamespace(service=SimpleNamespace(process=parent)))
            self.assertIsNotNone(parent.poll(), 'parent (chromedriver) still running')
            deadline = time.time() + 5
            alive = None
            while time.time() < deadline:
                alive = [p for p in (child, grandchild)
                         if (env['_proc_stat'](p) or ('', 'gone', 0))[1] not in ('Z', 'X', 'gone')]
                if not alive:
                    break
                time.sleep(0.05)
            self.assertEqual(alive, [], 'chrome/renderer survived')
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait()


if __name__ == '__main__':
    unittest.main()
