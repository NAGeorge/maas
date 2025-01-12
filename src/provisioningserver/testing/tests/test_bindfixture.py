# Copyright 2012-2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for the BIND fixture."""


import os
from subprocess import check_output

from testtools.matchers import Contains, FileExists, Not
from testtools.testcase import gather_details

from maastesting.matchers import FileContains
from maastesting.testcase import MAASTestCase
from provisioningserver.testing.bindfixture import (
    BINDServer,
    BINDServerResources,
)
from provisioningserver.utils.shell import get_env_with_locale


def dig_call(port=53, server="localhost", commands=None):
    """Call `dig` with the given command.

    Note that calling dig without a command will perform an NS
    query for "." (the root) which is useful to check if there
    is a running server.

    :param port: Port of the queried DNS server (defaults to 53).
    :param server: IP address of the queried DNS server (defaults
        to '127.0.0.1').
    :param commands: List of dig commands to run (defaults to None
        which will perform an NS query for "." (the root)).
    :return: The output as a string.
    :rtype: str
    """
    # The time and tries below are high so that tests pass in environments
    # that are much slower than the average developer's machine, so beware
    # before lowering. Many Bothans died to discover these parameters.
    cmd = ["dig", "+time=10", "+tries=5", f"@{server}", "-p", str(port)]
    if commands is not None:
        cmd.extend(commands)
    output = check_output(cmd, env=get_env_with_locale())
    return output.decode("utf-8").strip()


class TestBINDFixture(MAASTestCase):
    def test_start_check_shutdown(self):
        # The fixture correctly starts and stops BIND.
        with BINDServer() as fixture:
            try:
                result = dig_call(fixture.config.port)
                self.assertIn("Got answer", result)
            except Exception:
                # self.useFixture() is not being used because we want to
                # handle the fixture's lifecycle, so we must also be
                # responsible for propagating fixture details.
                gather_details(fixture.getDetails(), self.getDetails())
                raise
        self.assertFalse(fixture.runner.is_running())

    def test_config(self):
        # The configuration can be passed in.
        config = BINDServerResources()
        fixture = self.useFixture(BINDServer(config))
        self.assertIs(config, fixture.config)


class TestBINDServerResources(MAASTestCase):
    def test_defaults(self):
        with BINDServerResources() as resources:
            self.assertIsInstance(resources.port, int)
            self.assertIsInstance(resources.rndc_port, int)
            self.assertIsInstance(resources.homedir, str)
            self.assertIsInstance(resources.log_file, str)
            self.assertIs(resources.include_in_options, None)
            self.assertIsInstance(resources.named_file, str)
            self.assertIsInstance(resources.conf_file, str)
            self.assertIsInstance(resources.rndcconf_file, str)

    def test_setUp_copies_executable(self):
        with BINDServerResources() as resources:
            self.assertThat(resources.named_file, FileExists())

    def test_setUp_creates_config_files(self):
        with BINDServerResources() as resources:
            self.assertThat(
                resources.conf_file,
                FileContains(
                    matcher=Contains(b"listen-on port %d" % resources.port)
                ),
            )
            self.assertThat(
                resources.rndcconf_file,
                FileContains(
                    matcher=Contains(
                        b"default-port %d" % (resources.rndc_port)
                    )
                ),
            )
            # This should ideally be in its own test but it's here to cut
            # test run time. See test_setUp_honours_include_in_options()
            # as its counterpart.
            self.assertThat(
                resources.conf_file,
                Not(FileContains(matcher=Contains("forwarders"))),
            )

    def test_setUp_honours_include_in_options(self):
        forwarders = "forwarders { 1.2.3.4; };"
        with BINDServerResources(include_in_options=forwarders) as resources:
            expected_in_file = (resources.homedir + "/" + forwarders).encode(
                "ascii"
            )
            self.assertThat(
                resources.conf_file,
                FileContains(matcher=Contains(expected_in_file)),
            )

    def test_defaults_reallocated_after_teardown(self):
        seen_homedirs = set()
        resources = BINDServerResources()
        for _ in range(2):
            with resources:
                self.assertTrue(os.path.exists(resources.homedir))
                self.assertNotIn(resources.homedir, seen_homedirs)
                seen_homedirs.add(resources.homedir)
