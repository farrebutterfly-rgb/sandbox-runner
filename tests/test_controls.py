"""Every control, executed for real, against every backend that works on this machine.

A backend that is missing or does not work here is skipped with "not verified", never passed.
"""
import unittest

from sandbox_runner import checks
from sandbox_runner.runner import probe


def make_case(backend_name: str):
    class Case(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            cls.backend = probe(backend_name)
            if cls.backend is None:
                raise unittest.SkipTest(f"{backend_name} not available here: not verified")

        def check(self, fn):
            r = fn(self.backend)
            self.assertTrue(r["passed"], f"{backend_name} {r['control']}: {r['detail']}")

        def test_deterministic_result(self):
            self.check(checks.deterministic_result)

        def test_wall_timeout_busy_and_sleeping(self):
            self.check(checks.wall_timeout)

        def test_timeout_kills_whole_process_tree(self):
            self.check(checks.timeout_kills_process_tree)

        def test_cpu_limit(self):
            self.check(checks.cpu_limit)

        def test_memory_bomb_stopped(self):
            self.check(checks.memory_limit)

        def test_file_size_limit(self):
            self.check(checks.file_size_limit)

        def test_max_processes(self):
            self.check(checks.max_processes)

        def test_output_truncated(self):
            self.check(checks.output_truncated)

        def test_canary_outside_workdir_unreadable(self):
            self.check(checks.canary_unreadable)

        def test_tcp_to_host_test_server_refused(self):
            self.check(checks.network_refused)

        def test_next_run_has_no_leftover_files_or_processes(self):
            self.check(checks.no_leftovers)

        def test_env_has_no_secrets(self):
            self.check(checks.env_scrubbed)

        def test_host_invisible_root_readonly_no_caps(self):
            self.check(checks.host_invisible)

    Case.__name__ = Case.__qualname__ = f"Controls_{backend_name}"
    return Case


Controls_bwrap = make_case("bwrap")
Controls_unshare = make_case("unshare")

if __name__ == "__main__":
    unittest.main()
