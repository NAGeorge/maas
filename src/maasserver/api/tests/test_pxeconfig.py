# Copyright 2013-2014 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Tests for PXE configuration retrieval from the API."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = []

import httplib
import json

from crochet import TimeoutError
from django.core.urlresolvers import reverse
from django.test.client import RequestFactory
from maasserver import (
    preseed as preseed_module,
    server_address,
    )
from maasserver.api import pxeconfig as pxeconfig_module
from maasserver.api.pxeconfig import (
    find_nodegroup_for_pxeconfig_request,
    get_boot_image,
    get_boot_purpose,
    )
from maasserver.clusterrpc.testing.boot_images import make_rpc_boot_image
from maasserver.enum import (
    BOOT_RESOURCE_TYPE,
    NODE_BOOT,
    NODE_STATUS,
    NODEGROUPINTERFACE_MANAGEMENT,
    )
from maasserver.models import (
    Config,
    MACAddress,
    )
from maasserver.preseed import (
    compose_enlistment_preseed_url,
    compose_preseed_url,
    )
from maasserver.testing.architecture import make_usable_architecture
from maasserver.testing.factory import factory
from maasserver.testing.osystems import make_usable_osystem
from maasserver.testing.testcase import MAASServerTestCase
from maastesting.fakemethod import FakeMethod
from mock import sentinel
from netaddr import IPNetwork
from provisioningserver import kernel_opts
from provisioningserver.drivers.osystem import BOOT_IMAGE_PURPOSE
from provisioningserver.kernel_opts import KernelParameters
from provisioningserver.rpc.exceptions import NoConnectionsAvailable
from testtools.matchers import (
    Contains,
    ContainsAll,
    Equals,
    MatchesListwise,
    StartsWith,
    )


class TestGetBootImage(MAASServerTestCase):

    def test__returns_None_when_connection_unavailable(self):
        self.patch(
            pxeconfig_module,
            'get_boot_images_for').side_effect = NoConnectionsAvailable
        self.assertEqual(
            None,
            get_boot_image(
                sentinel.nodegroup, sentinel.osystem,
                sentinel.architecture, sentinel.subarchitecture,
                sentinel.series, sentinel.purpose))

    def test__returns_None_when_timeout_error(self):
        self.patch(
            pxeconfig_module,
            'get_boot_images_for').side_effect = TimeoutError
        self.assertEqual(
            None,
            get_boot_image(
                sentinel.nodegroup, sentinel.osystem,
                sentinel.architecture, sentinel.subarchitecture,
                sentinel.series, sentinel.purpose))

    def test__returns_matching_image(self):
        subarch = factory.make_name('subarch')
        purpose = factory.make_name('purpose')
        boot_image = make_rpc_boot_image(
            subarchitecture=subarch, purpose=purpose)
        other_images = [make_rpc_boot_image() for _ in range(3)]
        self.patch(
            pxeconfig_module,
            'get_boot_images_for').return_value = other_images + [boot_image]
        self.assertEqual(
            boot_image,
            get_boot_image(
                sentinel.nodegroup, sentinel.osystem,
                sentinel.architecture, subarch,
                sentinel.series, purpose))

    def test__returns_None_on_no_matching_image(self):
        subarch = factory.make_name('subarch')
        purpose = factory.make_name('purpose')
        other_images = [make_rpc_boot_image() for _ in range(3)]
        self.patch(
            pxeconfig_module,
            'get_boot_images_for').return_value = other_images
        self.assertEqual(
            None,
            get_boot_image(
                sentinel.nodegroup, sentinel.osystem,
                sentinel.architecture, subarch,
                sentinel.series, purpose))


class TestPXEConfigAPI(MAASServerTestCase):

    def get_default_params(self, nodegroup=None):
        if nodegroup is None:
            nodegroup = factory.make_NodeGroup()
        return {
            "local": factory.make_ipv4_address(),
            "remote": factory.make_ipv4_address(),
            "cluster_uuid": nodegroup.uuid,
            }

    def get_mac_params(self):
        params = self.get_default_params()
        params['mac'] = factory.make_MACAddress().mac_address
        return params

    def get_pxeconfig(self, params=None):
        """Make a request to `pxeconfig`, and return its response dict."""
        if params is None:
            params = self.get_default_params()
        response = self.client.get(reverse('pxeconfig'), params)
        return json.loads(response.content)

    def test_pxeconfig_returns_json(self):
        params = self.get_default_params()
        response = self.client.get(
            reverse('pxeconfig'), params)
        self.assertThat(
            (
                response.status_code,
                response['Content-Type'],
                response.content,
                response.content,
            ),
            MatchesListwise(
                (
                    Equals(httplib.OK),
                    Equals("application/json"),
                    StartsWith(b'{'),
                    Contains('arch'),
                )),
            response)

    def test_pxeconfig_returns_all_kernel_parameters(self):
        params = self.get_default_params()
        self.assertThat(
            self.get_pxeconfig(params),
            ContainsAll(KernelParameters._fields))

    def test_pxeconfig_returns_success_for_known_node(self):
        params = self.get_mac_params()
        response = self.client.get(reverse('pxeconfig'), params)
        self.assertEqual(httplib.OK, response.status_code)

    def test_pxeconfig_returns_no_content_for_unknown_node(self):
        params = dict(mac=factory.make_mac_address(delimiter='-'))
        response = self.client.get(reverse('pxeconfig'), params)
        self.assertEqual(httplib.NO_CONTENT, response.status_code)

    def test_pxeconfig_returns_success_for_detailed_but_unknown_node(self):
        architecture = make_usable_architecture(self)
        arch, subarch = architecture.split('/')
        nodegroup = factory.make_NodeGroup()
        params = dict(
            self.get_default_params(),
            mac=factory.make_mac_address(delimiter='-'),
            arch=arch,
            subarch=subarch,
            cluster_uuid=nodegroup.uuid)
        response = self.client.get(reverse('pxeconfig'), params)
        self.assertEqual(httplib.OK, response.status_code)

    def test_pxeconfig_returns_global_kernel_params_for_enlisting_node(self):
        # An 'enlisting' node means it looks like a node with details but we
        # don't know about it yet.  It should still receive the global
        # kernel options.
        value = factory.make_string()
        Config.objects.set_config("kernel_opts", value)
        architecture = make_usable_architecture(self)
        arch, subarch = architecture.split('/')
        nodegroup = factory.make_NodeGroup()
        params = dict(
            self.get_default_params(),
            mac=factory.make_mac_address(delimiter='-'),
            arch=arch,
            subarch=subarch,
            cluster_uuid=nodegroup.uuid)
        response = self.client.get(reverse('pxeconfig'), params)
        response_dict = json.loads(response.content)
        self.assertEqual(value, response_dict['extra_opts'])

    def test_pxeconfig_uses_present_boot_image(self):
        osystem = Config.objects.get_config('commissioning_osystem')
        release = Config.objects.get_config('commissioning_distro_series')
        resource_name = '%s/%s' % (osystem, release)
        factory.make_usable_boot_resource(
            rtype=BOOT_RESOURCE_TYPE.SYNCED,
            name=resource_name, architecture='amd64/generic')
        params = self.get_default_params()
        params_out = self.get_pxeconfig(params)
        self.assertEqual("amd64", params_out["arch"])

    def test_pxeconfig_defaults_to_i386_for_default(self):
        # As a lowest-common-denominator, i386 is chosen when the node is not
        # yet known to MAAS.
        expected_arch = tuple(
            make_usable_architecture(
                self, arch_name="i386", subarch_name="generic").split("/"))
        params = self.get_default_params()
        params_out = self.get_pxeconfig(params)
        observed_arch = params_out["arch"], params_out["subarch"]
        self.assertEqual(expected_arch, observed_arch)

    def test_pxeconfig_uses_fixed_hostname_for_enlisting_node(self):
        params = self.get_default_params()
        self.assertEqual(
            'maas-enlist', self.get_pxeconfig(params).get('hostname'))

    def test_pxeconfig_uses_enlistment_domain_for_enlisting_node(self):
        params = self.get_default_params()
        self.assertEqual(
            Config.objects.get_config('enlistment_domain'),
            self.get_pxeconfig(params).get('domain'))

    def test_pxeconfig_splits_domain_from_node_hostname(self):
        host = factory.make_name('host')
        domain = factory.make_name('domain')
        full_hostname = '.'.join([host, domain])
        node = factory.make_Node(hostname=full_hostname)
        mac = factory.make_MACAddress(node=node)
        params = self.get_default_params()
        params['mac'] = mac.mac_address
        pxe_config = self.get_pxeconfig(params)
        self.assertEqual(host, pxe_config.get('hostname'))
        self.assertNotIn(domain, pxe_config.values())

    def test_pxeconfig_uses_nodegroup_domain_for_node(self):
        mac = factory.make_MACAddress()
        params = self.get_default_params()
        params['mac'] = mac
        self.assertEqual(
            mac.node.nodegroup.name,
            self.get_pxeconfig(params).get('domain'))

    def get_without_param(self, param):
        """Request a `pxeconfig()` response, but omit `param` from request."""
        params = self.get_params()
        del params[param]
        return self.client.get(reverse('pxeconfig'), params)

    def silence_get_ephemeral_name(self):
        # Silence `get_ephemeral_name` to avoid having to fetch the
        # ephemeral name from the filesystem.
        self.patch(
            kernel_opts, 'get_ephemeral_name',
            FakeMethod(result=factory.make_string()))

    def test_pxeconfig_has_enlistment_preseed_url_for_default(self):
        self.silence_get_ephemeral_name()
        params = self.get_default_params()
        response = self.client.get(reverse('pxeconfig'), params)
        self.assertEqual(
            compose_enlistment_preseed_url(),
            json.loads(response.content)["preseed_url"])

    def test_pxeconfig_enlistment_preseed_url_detects_request_origin(self):
        self.silence_get_ephemeral_name()
        hostname = factory.make_hostname()
        ng_url = 'http://%s' % hostname
        network = IPNetwork("10.1.1/24")
        ip = factory.pick_ip_in_network(network)
        self.patch(server_address, 'resolve_hostname').return_value = {ip}
        factory.make_NodeGroup(
            maas_url=ng_url, network=network,
            management=NODEGROUPINTERFACE_MANAGEMENT.DHCP)
        params = self.get_default_params()
        del params['cluster_uuid']

        # Simulate that the request originates from ip by setting
        # 'REMOTE_ADDR'.
        response = self.client.get(
            reverse('pxeconfig'), params, REMOTE_ADDR=ip)
        self.assertThat(
            json.loads(response.content)["preseed_url"],
            StartsWith(ng_url))

    def test_pxeconfig_enlistment_log_host_url_detects_request_origin(self):
        self.silence_get_ephemeral_name()
        hostname = factory.make_hostname()
        ng_url = 'http://%s' % hostname
        network = IPNetwork("10.1.1/24")
        ip = factory.pick_ip_in_network(network)
        mock = self.patch(server_address, 'resolve_hostname')
        mock.return_value = {ip}
        factory.make_NodeGroup(
            maas_url=ng_url, network=network,
            management=NODEGROUPINTERFACE_MANAGEMENT.DHCP)
        params = self.get_default_params()
        del params['cluster_uuid']

        # Simulate that the request originates from ip by setting
        # 'REMOTE_ADDR'.
        response = self.client.get(
            reverse('pxeconfig'), params, REMOTE_ADDR=ip)
        self.assertEqual(
            (ip, hostname),
            (json.loads(response.content)["log_host"], mock.call_args[0][0]))

    def test_pxeconfig_has_preseed_url_for_known_node(self):
        params = self.get_mac_params()
        node = MACAddress.objects.get(mac_address=params['mac']).node
        response = self.client.get(reverse('pxeconfig'), params)
        self.assertEqual(
            compose_preseed_url(node),
            json.loads(response.content)["preseed_url"])

    def test_find_nodegroup_for_pxeconfig_request_uses_cluster_uuid(self):
        # find_nodegroup_for_pxeconfig_request returns the nodegroup
        # identified by the cluster_uuid parameter, if given.  It
        # completely ignores the other node or request details, as shown
        # here by passing a uuid for a different cluster.
        params = self.get_mac_params()
        nodegroup = factory.make_NodeGroup()
        params['cluster_uuid'] = nodegroup.uuid
        request = RequestFactory().get(reverse('pxeconfig'), params)
        self.assertEqual(
            nodegroup,
            find_nodegroup_for_pxeconfig_request(request))

    def test_preseed_url_for_known_node_uses_nodegroup_maas_url(self):
        ng_url = 'http://%s' % factory.make_name('host')
        network = IPNetwork("10.1.1/24")
        ip = factory.pick_ip_in_network(network)
        self.patch(server_address, 'resolve_hostname').return_value = {ip}
        nodegroup = factory.make_NodeGroup(maas_url=ng_url, network=network)
        params = self.get_mac_params()
        node = MACAddress.objects.get(mac_address=params['mac']).node
        node.nodegroup = nodegroup
        node.save()

        # Simulate that the request originates from ip by setting
        # 'REMOTE_ADDR'.
        response = self.client.get(
            reverse('pxeconfig'), params, REMOTE_ADDR=ip)
        self.assertThat(
            json.loads(response.content)["preseed_url"],
            StartsWith(ng_url))

    def test_get_boot_purpose_unknown_node(self):
        # A node that's not yet known to MAAS is assumed to be enlisting,
        # which uses a "commissioning" image.
        self.assertEqual("commissioning", get_boot_purpose(None))

    def test_get_boot_purpose_known_node(self):
        # The following table shows the expected boot "purpose" for each set
        # of node parameters.
        options = [
            ("poweroff", {"status": NODE_STATUS.NEW}),
            ("commissioning", {"status": NODE_STATUS.COMMISSIONING}),
            ("poweroff", {"status": NODE_STATUS.FAILED_COMMISSIONING}),
            ("poweroff", {"status": NODE_STATUS.MISSING}),
            ("poweroff", {"status": NODE_STATUS.READY}),
            ("poweroff", {"status": NODE_STATUS.RESERVED}),
            ("install", {"status": NODE_STATUS.DEPLOYING, "netboot": True}),
            ("xinstall", {"status": NODE_STATUS.DEPLOYING, "netboot": True}),
            ("local", {"status": NODE_STATUS.DEPLOYING, "netboot": False}),
            ("local", {"status": NODE_STATUS.DEPLOYED}),
            ("poweroff", {"status": NODE_STATUS.RETIRED}),
            ]
        node = factory.make_Node(boot_type=NODE_BOOT.DEBIAN)
        mock_get_boot_images_for = self.patch(
            preseed_module, 'get_boot_images_for')
        for purpose, parameters in options:
            boot_image = make_rpc_boot_image(purpose=purpose)
            mock_get_boot_images_for.return_value = [boot_image]
            if purpose == "xinstall":
                node.boot_type = NODE_BOOT.FASTPATH
            for name, value in parameters.items():
                setattr(node, name, value)
            self.assertEqual(purpose, get_boot_purpose(node))

    def test_get_boot_purpose_osystem_no_xinstall_support(self):
        osystem = make_usable_osystem(
            self, purposes=[BOOT_IMAGE_PURPOSE.INSTALL])
        release = factory.pick_release(osystem)
        node = factory.make_Node(
            status=NODE_STATUS.DEPLOYING, netboot=True,
            osystem=osystem.name, distro_series=release,
            boot_type=NODE_BOOT.FASTPATH)
        boot_image = make_rpc_boot_image(purpose='install')
        self.patch(
            preseed_module, 'get_boot_images_for').return_value = [boot_image]
        self.assertEqual('install', get_boot_purpose(node))

    def test_pxeconfig_uses_boot_purpose(self):
        fake_boot_purpose = factory.make_name("purpose")
        self.patch(
            pxeconfig_module, "get_boot_purpose"
            ).return_value = fake_boot_purpose
        params = self.get_default_params()
        response = self.client.get(reverse('pxeconfig'), params)
        self.assertEqual(
            fake_boot_purpose,
            json.loads(response.content)["purpose"])

    def test_pxeconfig_returns_fs_host_as_cluster_controller(self):
        # The kernel parameter `fs_host` points to the cluster controller
        # address, which is passed over within the `local` parameter.
        params = self.get_default_params()
        kernel_params = KernelParameters(**self.get_pxeconfig(params))
        self.assertEqual(params["local"], kernel_params.fs_host)

    def test_pxeconfig_returns_extra_kernel_options(self):
        node = factory.make_Node()
        extra_kernel_opts = factory.make_string()
        Config.objects.set_config('kernel_opts', extra_kernel_opts)
        mac = factory.make_MACAddress(node=node)
        params = self.get_default_params()
        params['mac'] = mac.mac_address
        pxe_config = self.get_pxeconfig(params)
        self.assertEqual(extra_kernel_opts, pxe_config['extra_opts'])

    def test_pxeconfig_returns_None_for_extra_kernel_opts(self):
        mac = factory.make_MACAddress()
        params = self.get_default_params()
        params['mac'] = mac.mac_address
        pxe_config = self.get_pxeconfig(params)
        self.assertEqual(None, pxe_config['extra_opts'])

    def test_pxeconfig_sets_nonsense_label_for_insane_state(self):
        # If pxeconfig() encounters a state where there is no relevant
        # BootImage for a given set of (nodegroup, arch, subarch,
        # release, purpose) it sets the label to no-such-image. This is
        # clearly nonsensical, but this state only arises during tests
        # or an insane environment.
        mac = factory.make_MACAddress()
        params = self.get_default_params()
        params['mac'] = mac.mac_address
        params['arch'] = 'iHaveNoIdea'
        pxe_config = self.get_pxeconfig(params)
        self.assertEqual('no-such-image', pxe_config['label'])

    def test_pxeconfig_returns_image_subarch_not_node_subarch(self):
        # In the scenario such as deploying trusty on an hwe-s subarch
        # node, the code will have fallen back to using trusty's generic
        # image as per the supported_subarches on the image. However,
        # pxeconfig needs to make sure the image path refers to the
        # subarch from the image, rather than the requested one.
        osystem = 'ubuntu'
        release = Config.objects.get_config('default_distro_series')
        nodegroup = factory.make_NodeGroup()
        generic_image = make_rpc_boot_image(
            osystem=osystem, release=release,
            architecture="amd64", subarchitecture="generic",
            purpose='install')
        hwe_s_image = make_rpc_boot_image(
            osystem=osystem, release=release,
            architecture="amd64", subarchitecture="hwe-s",
            purpose='install')
        self.patch(
            preseed_module,
            'get_boot_images_for').return_value = [generic_image, hwe_s_image]
        self.patch(
            pxeconfig_module,
            'get_boot_images_for').return_value = [generic_image, hwe_s_image]
        node = factory.make_Node(
            mac=True, nodegroup=nodegroup, status=NODE_STATUS.DEPLOYING,
            architecture="amd64/hwe-s")
        params = self.get_default_params()
        params['cluster_uuid'] = nodegroup.uuid
        params['mac'] = node.get_primary_mac()
        params['arch'] = "amd64"
        params['subarch'] = "hwe-s"

        params_out = self.get_pxeconfig(params)
        self.assertEqual("hwe-s", params_out["subarch"])
