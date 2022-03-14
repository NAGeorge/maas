# Copyright 2020 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

from datetime import datetime, timedelta
from pathlib import Path

from fixtures import EnvironmentVariable, TempDir
from OpenSSL import crypto

from maastesting.factory import factory
from maastesting.testcase import MAASTestCase
from provisioningserver.certificates import (
    Certificate,
    CertificateError,
    get_maas_cert_tuple,
)
from provisioningserver.testing.certificates import get_sample_cert


class TestCertificate(MAASTestCase):
    def setUp(self):
        super().setUp()
        self.sample_cert = get_sample_cert("maas")

    def test_certificate(self):
        self.assertEqual(self.sample_cert.cn(), "maas")
        self.assertGreaterEqual(
            datetime.utcnow() + timedelta(days=3650),
            self.sample_cert.expiration(),
        )
        self.assertTrue(
            self.sample_cert.certificate_pem().startswith(
                "-----BEGIN CERTIFICATE-----"
            )
        )
        self.assertTrue(
            self.sample_cert.public_key_pem().startswith(
                "-----BEGIN PUBLIC KEY-----"
            )
        )
        self.assertTrue(
            self.sample_cert.private_key_pem().startswith(
                "-----BEGIN PRIVATE KEY-----"
            )
        )

    def test_from_pem_single_material(self):
        cert = Certificate.from_pem(
            self.sample_cert.certificate_pem()
            + self.sample_cert.private_key_pem()
        )
        self.assertEqual(
            self.sample_cert.certificate_pem(), cert.certificate_pem()
        )
        self.assertEqual(
            self.sample_cert.private_key_pem(), cert.private_key_pem()
        )

    def test_from_pem_multiple_material(self):
        cert = Certificate.from_pem(
            self.sample_cert.certificate_pem(),
            self.sample_cert.private_key_pem(),
        )
        self.assertEqual(
            self.sample_cert.certificate_pem(), cert.certificate_pem()
        )
        self.assertEqual(
            self.sample_cert.private_key_pem(), cert.private_key_pem()
        )

    def test_from_pem_multiple_material_adds_newlines(self):
        # material entries are joined with a newline since each PEM material
        # must start on a new line to be valid
        cert = Certificate.from_pem(
            self.sample_cert.certificate_pem().strip(),
            self.sample_cert.private_key_pem().strip(),
        )
        self.assertEqual(
            self.sample_cert.certificate_pem(), cert.certificate_pem()
        )
        self.assertEqual(
            self.sample_cert.private_key_pem(), cert.private_key_pem()
        )

    def test_from_pem_invalid_material(self):
        error = self.assertRaises(
            CertificateError, Certificate.from_pem, "random stuff"
        )
        self.assertEqual(str(error), "Invalid PEM material")

    def test_from_pem_private_key_with_passphrase(self):
        encrypted_privatekey_pem = crypto.dump_privatekey(
            crypto.FILETYPE_PEM,
            self.sample_cert.key,
            cipher="AES128",
            passphrase=b"sekret",
        ).decode("ascii")
        error = self.assertRaises(
            CertificateError,
            Certificate.from_pem,
            self.sample_cert.certificate_pem(),
            encrypted_privatekey_pem,
        )
        self.assertEqual(str(error), "Private key can't have a passphrase")

    def test_from_pem_no_key(self):
        error = self.assertRaises(
            CertificateError,
            Certificate.from_pem,
            self.sample_cert.certificate_pem(),
        )
        self.assertEqual(str(error), "Invalid PEM material")

    def test_from_pem_check_keys_match(self):
        cert = Certificate.generate("maas")
        material = self.sample_cert.certificate_pem() + cert.private_key_pem()
        error = self.assertRaises(
            CertificateError,
            Certificate.from_pem,
            material,
        )
        self.assertEqual(str(error), "Private and public keys don't match")

    def test_tempfiles(self):
        cert_file, key_file = self.sample_cert.tempfiles()
        self.assertEqual(
            Path(cert_file).read_text(), self.sample_cert.certificate_pem()
        )
        self.assertEqual(
            Path(key_file).read_text(), self.sample_cert.private_key_pem()
        )

    def test_generate_certificate_defaults(self):
        cert = Certificate.generate("maas")
        self.assertIsInstance(cert.cert, crypto.X509)
        self.assertIsInstance(cert.key, crypto.PKey)
        self.assertEqual(cert.cert.get_subject().CN, "maas")
        self.assertIsNone(cert.cert.get_issuer().organizationName)
        self.assertIsNone(cert.cert.get_issuer().organizationalUnitName)
        self.assertEqual(
            crypto.dump_publickey(crypto.FILETYPE_PEM, cert.cert.get_pubkey()),
            crypto.dump_publickey(crypto.FILETYPE_PEM, cert.key),
        )
        self.assertEqual(cert.key.bits(), 4096)
        self.assertEqual(cert.key.type(), crypto.TYPE_RSA)
        self.assertGreaterEqual(
            datetime.utcnow() + timedelta(days=3650),
            cert.expiration(),
        )

    def test_generate_certificate_key_bits(self):
        cert = Certificate.generate("maas", key_bits=1024)
        self.assertEqual(cert.key.bits(), 1024)

    def test_generate_certificate_validity(self):
        cert = Certificate.generate("maas", validity=timedelta(days=100))
        self.assertGreaterEqual(
            datetime.utcnow() + timedelta(days=100),
            cert.expiration(),
        )

    def test_generate_certificate_organization(self):
        cert = Certificate.generate(
            "maas",
            organization_name="myorg",
            organizational_unit_name="myunit",
        )
        self.assertEqual(cert.cert.get_issuer().organizationName, "myorg")
        self.assertEqual(
            cert.cert.get_issuer().organizationalUnitName, "myunit"
        )

    def test_generate_truncate_fields(self):
        cn = factory.make_string(size=65)
        o = factory.make_string(size=65)
        ou = factory.make_string(size=65)
        cert = Certificate.generate(
            cn, organization_name=o, organizational_unit_name=ou
        )
        # max fields length is 64, so the last char is truncated
        self.assertEqual(cert.cn(), cn[:-1])
        self.assertEqual(cert.o(), o[:-1])
        self.assertEqual(cert.ou(), ou[:-1])


class TestGetMAASCertTuple(MAASTestCase):
    def setUp(self):
        super().setUp()
        self.tempdir = Path(self.useFixture(TempDir()).path)

    def test_get_maas_cert_tuple_missing_files(self):
        self.useFixture(EnvironmentVariable("MAAS_ROOT", str(self.tempdir)))
        self.useFixture(EnvironmentVariable("SNAP", None))
        self.assertIsNone(get_maas_cert_tuple())

    def test_get_maas_cert_tuple(self):
        certs_dir = self.tempdir / "etc/maas/certificates"
        certs_dir.mkdir(parents=True)
        (certs_dir / "maas.crt").touch()
        (certs_dir / "maas.key").touch()
        self.useFixture(EnvironmentVariable("MAAS_ROOT", str(self.tempdir)))
        self.useFixture(EnvironmentVariable("SNAP", None))
        self.assertEqual(
            get_maas_cert_tuple(),
            (
                f"{certs_dir}/maas.crt",
                f"{certs_dir}/maas.key",
            ),
        )

    def test_get_maas_cert_tuple_snap(self):
        certs_dir = self.tempdir / "certificates"
        certs_dir.mkdir(parents=True)
        (certs_dir / "maas.crt").touch()
        (certs_dir / "maas.key").touch()
        self.useFixture(EnvironmentVariable("SNAP_COMMON", str(self.tempdir)))
        self.useFixture(EnvironmentVariable("SNAP", "/snap/maas/current"))
        self.assertEqual(
            get_maas_cert_tuple(),
            (
                f"{certs_dir}/maas.crt",
                f"{certs_dir}/maas.key",
            ),
        )
