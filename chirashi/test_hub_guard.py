import unittest
from hub_guard import decision


class GuardTests(unittest.TestCase):
    def test_requires_three_failures(self):
        state = {}
        for tick in range(2):
            state, restart = decision(state, False, 10000 + tick * 60, 'day')
            self.assertFalse(restart)
        state, restart = decision(state, False, 10120, 'day')
        self.assertTrue(restart)
        self.assertEqual(state['restarts_today'], 1)

    def test_cooldown_and_limit(self):
        state = dict(day='day', consecutive_failures=4, restarts_today=1, last_restart=10000)
        self.assertFalse(decision(state, False, 10100, 'day')[1])
        state['restarts_today'] = 3
        self.assertFalse(decision(state, False, 20000, 'day')[1])

    def test_healthy_resets_failures(self):
        state, restart = decision({'consecutive_failures':9}, True, 10000, 'day')
        self.assertEqual(state['consecutive_failures'], 0)
        self.assertFalse(restart)

    def test_new_day_resets_budget(self):
        state, restart = decision(dict(day='old', restarts_today=3, consecutive_failures=4), False, 20000, 'new')
        self.assertTrue(restart)
        self.assertEqual(state['restarts_today'], 1)


if __name__ == '__main__':
    unittest.main()
