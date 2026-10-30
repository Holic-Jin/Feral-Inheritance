"""Unit tests for tools/model_gateway.py (stdlib unittest, no real network).

Fake credentials are assembled at runtime so this file itself never contains
a secret-looking literal (keeps GitHub push protection / secret scanning quiet).
"""
import contextlib
import io
import ipaddress
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
REPO = os.path.dirname(TOOLS)
sys.path.insert(0, TOOLS)
import model_gateway as gw  # noqa: E402

FAKE_NV_KEY = "nv" + "api-" + "Q7x" * 12
FAKE_GH_TOKEN = "gh" + "p_" + "aB3dE5" * 7


def cfg():
    with open(os.path.join(TOOLS, "gateway_config.json"), encoding="utf-8") as f:
        return json.load(f)


def make_repo(tmp):
    root = os.path.join(tmp, "feral-inheritance")
    os.makedirs(os.path.join(root, "Source"))
    os.makedirs(os.path.join(root, "Common", "Defs"))
    os.makedirs(os.path.join(root, "tools"))
    with open(os.path.join(root, "Source", "Foo.cs"), "w") as f:
        f.write("namespace FeralInheritance\n{\n    public static class Foo\n    {\n        public const int X = 1;\n    }\n}\n")
    with open(os.path.join(root, "Common", "Defs", "Bar.xml"), "w") as f:
        f.write('<?xml version="1.0" encoding="utf-8"?>\n<Defs>\n</Defs>\n')
    with open(os.path.join(root, ".gitignore"), "w") as f:
        f.write(".gateway/\n")
    run = lambda *a: subprocess.run(["git", "-C", root] + list(a), check=True, capture_output=True)
    run("init", "-q", "-b", "main")
    run("add", "-A")
    run("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", "init")
    # local bare repo as "origin" (file path, so no network is ever used)
    remote = os.path.join(tmp, "remote.git")
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", remote], check=True, capture_output=True)
    run("remote", "add", "origin", remote)
    run("push", "-q", "origin", "main")
    return root


def git_out(repo, *a):
    return subprocess.run(["git", "-C", repo] + list(a), capture_output=True, text=True, check=True).stdout.strip()


GOOD_DIFF = """diff --git a/Source/Foo.cs b/Source/Foo.cs
--- a/Source/Foo.cs
+++ b/Source/Foo.cs
@@ -2,6 +2,6 @@ namespace FeralInheritance
 {
     public static class Foo
     {
-        public const int X = 1;
+        public const int X = 2;
     }
 }
"""


def diff_for(path, extra_header=""):
    return ("diff --git a/{p} b/{p}\n{h}--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n-a\n+b\n"
            .format(p=path, h=extra_header))


def new_file_diff(path, mode="100644"):
    return ("diff --git a/{p} b/{p}\nnew file mode {m}\nindex 0000000..1111111\n--- /dev/null\n+++ b/{p}\n"
            "@@ -0,0 +1 @@\n+hello\n").format(p=path, m=mode)


class FakeResp(io.BytesIO):
    def __init__(self, payload, status=200):
        super().__init__(json.dumps(payload).encode())
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def model_reply(content, usage=None):
    return FakeResp({"choices": [{"message": {"content": content}}],
                     "usage": usage or {"prompt_tokens": 111, "completion_tokens": 22, "total_tokens": 133}})


_REAL_GETADDRINFO = socket.getaddrinfo
DNS_NAMES = []


def _host_of(address):
    if isinstance(address, tuple) and address:
        host = address[0]
    else:
        host = address
    if isinstance(host, bytes):
        try:
            host = host.decode("ascii")
        except UnicodeError:
            return None
    return host


def _ip_is_loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return None


def _is_loopback(address):
    """Literal loopback IPs and the name localhost. Every other name is rejected with no DNS."""
    host = _host_of(address)
    if not isinstance(host, str) or host == "":
        return False
    if host.lower() == "localhost":
        return True
    literal = _ip_is_loopback(host)
    return bool(literal)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg = cfg()
        env = {k: v for k, v in os.environ.items() if k not in ("NVIDIA_API_KEY", "GITHUB_TOKEN", "GITHUB_REPOSITORY", "GH_TOKEN")}
        # Local file remotes stay usable. https/git/ssh and a proxy to TEST-NET are not.
        env["GIT_ALLOW_PROTOCOL"] = "file"
        env["http_proxy"] = "http://192.0.2.1:9"
        env["https_proxy"] = "http://192.0.2.1:9"
        env["HTTP_PROXY"] = "http://192.0.2.1:9"
        env["HTTPS_PROXY"] = "http://192.0.2.1:9"
        env["ALL_PROXY"] = "http://192.0.2.1:9"
        env["NO_PROXY"] = ""
        env["no_proxy"] = ""
        self.env = mock.patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.repo = make_repo(self.tmp)
        # urlopen is not the only network path: the gateway uses build_opener().open.
        self.net = mock.patch("urllib.request.urlopen", side_effect=AssertionError("real network call"))
        self.net.start()
        import _socket
        real_create = socket.create_connection
        real_connect = _socket.socket.connect
        real_connect_ex = socket.socket.connect_ex
        real_sendto = socket.socket.sendto
        real_sendmsg = socket.socket.sendmsg
        real_bind = _socket.socket.bind
        orig_socket_type = _socket.socket

        class _GuardedCSocket(orig_socket_type):
            def connect(self, address):
                if not _is_loopback(address):
                    raise AssertionError("non-loopback connect %r" % (address,))
                return orig_socket_type.connect(self, address)

            def bind(self, address):
                if _host_of(address) in ("0.0.0.0", "::"):
                    raise AssertionError("refusing bind to %r" % (address,))
                return orig_socket_type.bind(self, address)

        # The C type itself is immutable, so replace the name callers look up.
        _socket.socket = _GuardedCSocket
        self._orig_socket_type = orig_socket_type
        self._socket_mod = _socket

        def guarded_create(address, *args, **kwargs):
            if not _is_loopback(address):
                raise AssertionError("non-loopback create_connection %r" % (address,))
            return real_create(address, *args, **kwargs)

        def guarded_connect(sock, address):
            if not _is_loopback(address):
                raise AssertionError("non-loopback connect %r" % (address,))
            return real_connect(sock, address)

        def guarded_connect_ex(sock, address):
            if not _is_loopback(address):
                raise AssertionError("non-loopback connect_ex %r" % (address,))
            return real_connect_ex(sock, address)

        def _send_address(args, kwargs):
            if "address" in kwargs:
                return kwargs["address"]
            if not args:
                return None
            last = args[-1]
            if isinstance(last, int):
                return None
            return last

        def guarded_sendto(sock, data, *args, **kwargs):
            address = _send_address(args, kwargs)
            if address is not None and not _is_loopback(address):
                raise AssertionError("non-loopback sendto %r" % (address,))
            return real_sendto(sock, data, *args, **kwargs)

        def guarded_sendmsg(sock, buffers, *args, **kwargs):
            address = kwargs.get("address")
            if address is None and len(args) >= 3:
                address = args[2]
            if address is not None and not _is_loopback(address):
                raise AssertionError("non-loopback sendmsg %r" % (address,))
            return real_sendmsg(sock, buffers, *args, **kwargs)

        def guarded_getaddrinfo(host, port, *args, **kwargs):
            if host not in (None, "", b"") and not _is_loopback((host, port)):
                raise AssertionError("non-loopback getaddrinfo %r" % (host,))
            if isinstance(host, str) and _ip_is_loopback(host) is None:
                DNS_NAMES.append(host)
            return _REAL_GETADDRINFO(host, port, *args, **kwargs)

        def guarded_bind(sock, address):
            host = _host_of(address)
            if host in ("0.0.0.0", "::"):
                raise AssertionError("refusing bind to %r" % (address,))
            return real_bind(sock, address)

        self._socket_guards = [
            mock.patch("socket.create_connection", guarded_create),
            mock.patch.object(socket.socket, "connect", guarded_connect),
            mock.patch.object(socket.socket, "connect_ex", guarded_connect_ex),
            mock.patch.object(socket.socket, "sendto", guarded_sendto),
            mock.patch.object(socket.socket, "sendmsg", guarded_sendmsg),
            mock.patch.object(socket.socket, "bind", guarded_bind),
            mock.patch("socket.getaddrinfo", guarded_getaddrinfo),
        ]
        for guard in self._socket_guards:
            guard.start()
        self.clock = Clock(1793000000.0)

    def tearDown(self):
        for guard in reversed(getattr(self, "_socket_guards", [])):
            guard.stop()
        if getattr(self, "_socket_mod", None) is not None:
            self._socket_mod.socket = self._orig_socket_type
        self.net.stop()
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def args(self, *extra):
        extra = list(extra)
        if "--task-id" not in extra:
            extra = ["--task-id", "t1"] + extra
        return gw.parse_args(["--repo-root", self.repo] + extra)

    def ledger(self):
        path = os.path.join(self.repo, ".gateway", "calls.jsonl")
        with open(path, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]

    def run_gw(self, args, opener=None, **kw):
        # default fake clock moves 1s per run, so back-to-back runs get distinct branch names
        if "clock" not in kw:
            kw["clock"], kw["sleep"] = self.clock, self.clock.sleep
            self.clock.t += 1
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = gw.run(args, self.cfg, opener=opener, out=out, **kw)
        return rc, out.getvalue(), err.getvalue()


class TestFinisMundiRefusal(Base):
    def test_config_contains_only_feral(self):
        self.assertEqual(self.cfg["allowed_projects"], ["feral-inheritance"])

    def test_project_name_refused_even_if_allowlisted(self):
        c = dict(self.cfg, allowed_projects=["feral-inheritance", "finismundi", "FinisMundi"])
        for name in ("finismundi", "FinisMundi", "FINISMUNDI", "finis-mundi", "Finis_Mundi", "my-finismundi-fork"):
            with self.assertRaises(gw.PolicyError) as cm:
                gw.check_project(name, c)
            self.assertEqual(cm.exception.outcome, "refused_finismundi")

    def test_repo_path_and_repo_env_refused(self):
        with self.assertRaises(gw.PolicyError):
            gw.check_project("feral-inheritance", self.cfg, "/srv/checkouts/FinisMundi/x")
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "someone/FinisMundi"}):
            with self.assertRaises(gw.PolicyError):
                gw.check_project("feral-inheritance", self.cfg)
        with self.assertRaises(gw.PolicyError):
            gw.open_pr("someone/finismundi", "t", "h", "main", "x", "y", opener=mock.Mock())

    def test_unknown_project_refused(self):
        with self.assertRaises(gw.PolicyError) as cm:
            gw.check_project("other-project", self.cfg)
        self.assertEqual(cm.exception.outcome, "refused_project")

    def test_run_refuses_without_network_and_logs(self):
        opener = mock.Mock()
        for argv in (["--project", "finismundi", "--task", "x"],
                     ["--task", "port the FinisMundi save system"],
                     ["--task-id", "finismundi-port", "--task", "x"],
                     ["--task", "x", "--files", "Source/FinisMundi.cs"]):
            rc, _, err = self.run_gw(self.args(*argv), opener=opener)
            self.assertEqual(rc, gw.EXIT_POLICY, argv)
            self.assertIn("FinisMundi", err)
        opener.assert_not_called()
        self.assertTrue(all(r["outcome"] == "refused_finismundi" for r in self.ledger()))
        # refused before any branch was created
        self.assertEqual(git_out(self.repo, "branch", "--list", "model/*"), "")

    def test_hardcoded_allowlist_cannot_be_widened(self):
        c = dict(self.cfg, allowed_projects=["feral-inheritance", "other-project"])
        with self.assertRaises(gw.PolicyError) as cm:
            gw.check_project("other-project", c)
        self.assertEqual(cm.exception.outcome, "refused_project")
        self.assertEqual(gw.ALLOWED_PROJECTS, ("feral-inheritance",))


class TestSecretFilter(Base):
    SAMPLES = {
        "aws_access_key_id": "AK" + "IA" + "ABCDEFGHIJKLMNOP",
        "github_token": FAKE_GH_TOKEN,
        "github_token_oauth": "gh" + "o_" + "x9Y8z7" * 6,
        "github_pat": "github" + "_pat_" + "11ABCDEFG0" * 4,
        "nvidia_api_key": FAKE_NV_KEY,
        "openai_style_key": "s" + "k-proj-" + "Zz9" * 10,
        "private_key_block": "-----BEGIN " + "RSA PRIVATE KEY-----\nMIIE\n",
        "openssh_key": "-----BEGIN " + "OPENSSH PRIVATE KEY-----",
        "slack_token": "xo" + "xb-" + "1234567890-abcdef",
        "env_style": "SERVICE_KEY=" + "f8Kq2LmZ9xP4vR7tB1nW6yH3",
        "env_style_generic_random": "FOO_VALUE=" + "f8Kq2LmZ9xP4vR7tB1nW6yH3cJ5",
        "aws_secret": "aws_secret_access_key = " + "wJalrXUtnFEMI/K7MDENG/" + "bPxRfiCYEXAMPLEKEY",
        "url_password": "https://user:" + "hunter22@example.com/x",
    }

    def test_each_pattern_detected(self):
        for name, value in self.SAMPLES.items():
            with self.subTest(name=name):
                self.assertTrue(gw.find_secrets("prefix\n%s\nsuffix" % value), name)

    def test_code_assignments_not_flagged(self):
        for code in ('api_key = os.environ.get("NVIDIA_API_KEY", "")',
                     "string tokenName = def.defName.ToStringSafe();",
                     "public static TraitDef FI_FeralInheritance;"):
            self.assertEqual(gw.find_secrets(code), [], code)

    def test_live_env_value_detected(self):
        with mock.patch.dict(os.environ, {"NVIDIA_API_KEY": "plainlookingvalue123"}):
            self.assertEqual(gw.find_secrets("x plainlookingvalue123 y", gw.live_secret_values()),
                             ["live_credential_value"])

    def test_no_false_positive_on_real_mod_sources(self):
        hits = {}
        for d, _, names in os.walk(REPO):
            if any(s in d for s in (os.sep + ".git", os.sep + "tools", os.sep + ".gateway")):
                continue
            for n in names:
                if n.endswith((".cs", ".xml", ".md", ".csproj")):
                    with open(os.path.join(d, n), encoding="utf-8") as f:
                        h = gw.find_secrets(f.read())
                    if h:
                        hits[n] = h
        self.assertEqual(hits, {})

    def test_secret_in_task_or_file_refused_and_not_echoed(self):
        opener = mock.Mock()
        rc, out, err = self.run_gw(self.args("--task", "use key " + FAKE_NV_KEY), opener=opener)
        self.assertEqual(rc, gw.EXIT_POLICY)
        self.assertNotIn(FAKE_NV_KEY, out + err)
        with open(os.path.join(self.repo, "Source", "Foo.cs"), "a") as f:
            f.write("// token " + FAKE_GH_TOKEN + "\n")
        # context files are read from the fresh branch (= origin/main), so publish the change there
        subprocess.run(["git", "-C", self.repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "commit", "-qam", "secret"], check=True)
        subprocess.run(["git", "-C", self.repo, "push", "-q", "origin", "HEAD:main"], check=True, capture_output=True)
        rc, out, err = self.run_gw(self.args("--task", "x", "--files", "Source/Foo.cs"), opener=opener)
        self.assertEqual(rc, gw.EXIT_POLICY)
        self.assertNotIn(FAKE_GH_TOKEN, out + err)
        opener.assert_not_called()
        self.assertEqual([r["outcome"] for r in self.ledger()], ["refused_secret", "refused_secret"])

    def test_secret_in_model_diff_rejected(self):
        d = GOOD_DIFF.replace("+        public const int X = 2;",
                              '+        public const string K = "' + FAKE_NV_KEY + '";')
        with self.assertRaises(gw.SecretDetected):
            gw.validate_diff(d, self.cfg, self.repo)


class TestPathPolicy(Base):
    def test_allowed_paths(self):
        for p in ("Source/Foo.cs", "Source/Sub/Dir/Bar.cs", "Common/Defs/FI_Abilities.xml",
                  "Common/Languages/Korean/Keyed/FI_Keys.xml", "Mods/Biotech/Defs/FI_Gene.xml",
                  "Mods/Biotech/Languages/Korean/DefInjected/GeneDef/FI_Gene.xml", "Common/Patches/P.xml",
                  "1.6/Defs/X.xml", "1.7/Patches/Y.xml", "1.7/Languages/English/Keyed/K.xml"):
            gw.check_write_path(p, self.cfg)

    def test_denied_paths(self):
        for p in (".github/workflows/test.yml", "tools/model_gateway.py", "tools/gateway_config.json",
                  "Source/FeralInheritance.csproj", "Directory.Build.props", "Source/Directory.Build.targets",
                  "NuGet.config", "nuget.config", "Source/NuGet.Config", "Source/x.props", "Source/x.targets",
                  "About/About.xml", "LoadFolders.xml", ".gitignore", "Source/.gitattributes",
                  "1.6/Assemblies/FeralInheritance.dll", "Source/build.sh", "Source/Foo.txt",
                  "README.md", "Source/FinisMundi.cs", "global.json", "Source/global.json",
                  "Source/packages.lock.json", "packages.lock.json", "Directory.Packages.props",
                  "Mods/Biotech/LoadFolders.xml", "About/Preview.png", "Source/obj/Gen.cs", "Source/bin/X.cs",
                  "1.6/Assemblies/X.cs", "1.6/Assemblies/X.xml", "Common/Defs/X.cs", "Source/X.xml",
                  "tools/tests/test_model_gateway.py", ".github/dependabot.yml", "Common/Textures/a.png",
                  "Common/Defs/FI_Abilities.XML.bak", "1.8/Defs/X.xml"):
            with self.subTest(p=p):
                with self.assertRaises(gw.DiffRejected):
                    gw.check_write_path(p, self.cfg)

    def test_deny_beats_allow_even_if_config_widened(self):
        c = dict(self.cfg, allowed_path_globs=["**"])
        for p in (".github/workflows/x.yml", "tools/model_gateway.py", "Source/FeralInheritance.csproj",
                  "README.md", "nuget.config"):
            with self.assertRaises(gw.DiffRejected):
                gw.check_write_path(p, c)

    def test_config_can_only_narrow(self):
        c = dict(self.cfg, allowed_path_globs=["Source/**/*.cs"])
        gw.check_write_path("Source/Foo.cs", c)
        with self.assertRaises(gw.DiffRejected):
            gw.check_write_path("Common/Defs/FI_Abilities.xml", c)

    def test_traversal_and_bad_syntax(self):
        for p in ("../etc/passwd", "Source/../tools/model_gateway.py", "Source/./Foo.cs", "/abs/Source/Foo.cs",
                  "Source\\Foo.cs", "Source//Foo.cs", "Source/-x.cs", "Source/F\u00f6o.cs", "Source/Foo.cs\x00"):
            with self.subTest(p=p):
                with self.assertRaises(gw.DiffRejected):
                    gw.check_write_path(p, self.cfg)

    def test_whole_diff_rejected_if_any_file_denied(self):
        d = GOOD_DIFF + diff_for(".github/workflows/test.yml")
        with self.assertRaises(gw.DiffRejected):
            gw.validate_diff(d, self.cfg, self.repo)
        d = GOOD_DIFF + diff_for("Source/FeralInheritance.csproj")
        with self.assertRaises(gw.DiffRejected):
            gw.validate_diff(d, self.cfg, self.repo)

    def test_traversal_inside_diff(self):
        d = ("--- a/Source/../tools/model_gateway.py\n+++ b/Source/../tools/model_gateway.py\n"
             "@@ -1 +1 @@\n-a\n+b\n")
        with self.assertRaises(gw.DiffRejected) as cm:
            gw.validate_diff(d, self.cfg, self.repo)
        self.assertEqual(cm.exception.outcome, "rejected_traversal")

    def test_header_mismatch_and_hidden_plus_header(self):
        # +++ points elsewhere than ---: both paths are checked
        d = "--- a/Source/Foo.cs\n+++ b/tools/evil.py\n@@ -1 +1 @@\n-a\n+b\n"
        with self.assertRaises(gw.DiffRejected):
            gw.validate_diff(d, self.cfg, self.repo)

    def test_modes_binary_symlink_rename_delete(self):
        bad = {
            "exec": new_file_diff("Source/New.cs", "100755"),
            "symlink": new_file_diff("Source/New.cs", "120000"),
            "mode_change": diff_for("Source/Foo.cs", "old mode 100644\nnew mode 100755\n"),
            "delete": "diff --git a/Source/Foo.cs b/Source/Foo.cs\ndeleted file mode 100644\n--- a/Source/Foo.cs\n+++ /dev/null\n@@ -1 +0,0 @@\n-a\n",
            "rename": "diff --git a/Source/Foo.cs b/Source/Bar.cs\nsimilarity index 100%\nrename from Source/Foo.cs\nrename to Source/Bar.cs\n",
            "binary": "diff --git a/Source/Foo.cs b/Source/Foo.cs\nGIT binary patch\nliteral 3\nKcmZ?\n",
            "binary2": "diff --git a/Source/Foo.cs b/Source/Foo.cs\nBinary files a/Source/Foo.cs and b/Source/Foo.cs differ\n@@\n",
            "nul": GOOD_DIFF.replace("X = 2", "X = \x002"),
            "quoted": '--- "a/Source/F\\303\\266o.cs"\n+++ "b/Source/F\\303\\266o.cs"\n@@ -1 +1 @@\n-a\n+b\n',
            "no_diff": "Sure! Here is the change you asked for.",
        }
        for name, d in bad.items():
            with self.subTest(name=name):
                with self.assertRaises(gw.DiffRejected):
                    gw.validate_diff(gw.extract_diff(d) if name == "no_diff" else d, self.cfg, self.repo)

    def test_symlink_on_disk_rejected(self):
        os.symlink(os.path.join(self.repo, "tools"), os.path.join(self.repo, "Source", "Link"))
        with self.assertRaises(gw.DiffRejected) as cm:
            gw.check_write_path("Source/Link/x.cs", self.cfg, self.repo)
        self.assertEqual(cm.exception.outcome, "rejected_symlink")

    def test_good_diff_and_new_file_pass_and_git_agrees(self):
        self.assertEqual(gw.validate_diff(GOOD_DIFF, self.cfg, self.repo), ["Source/Foo.cs"])
        nf = new_file_diff("Source/NewThing.cs")
        self.assertEqual(gw.validate_diff(nf, self.cfg, self.repo), ["Source/NewThing.cs"])
        p = os.path.join(self.tmp, "x.diff")
        with open(p, "w") as f:
            f.write(GOOD_DIFF)
        self.assertEqual(gw.git_check_numstat(self.repo, p, self.cfg), ["Source/Foo.cs"])

    def test_extract_diff_from_fenced_reply(self):
        reply = "Here you go:\n```diff\n" + GOOD_DIFF + "```\nThanks"
        self.assertEqual(gw.extract_diff(reply), GOOD_DIFF)

    def test_context_files_restricted(self):
        for p in ("tools/model_gateway.py", ".github/workflows/test.yml", "../x", "README.md"):
            with self.assertRaises(gw.GatewayError):
                gw.check_context_path(p, self.cfg, self.repo)
        gw.check_context_path("Source/Foo.cs", self.cfg, self.repo)


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class TestRateLimit(Base):
    def limiter(self, clock, wait=False, max_wait=90):
        return gw.RateLimiter(os.path.join(self.tmp, "state", "rate.json"), 40, 60, clock, clock.sleep, wait, max_wait)

    def test_40_per_rolling_minute_persisted_across_instances(self):
        c = Clock()
        for i in range(40):
            # new instance every call = new process invocation in the same job
            self.limiter(c).acquire()
            c.t += 0.5
        with self.assertRaises(gw.RateLimitExceeded):
            self.limiter(c).acquire()
        self.assertEqual(self.limiter(c).count(), 40)
        c.t = 1_000_000.0 + 60.01  # first call leaves the window
        self.limiter(c).acquire()
        with self.assertRaises(gw.RateLimitExceeded):
            self.limiter(c).acquire()

    def test_wait_mode_sleeps_until_slot(self):
        c = Clock()
        for _ in range(40):
            self.limiter(c).acquire()
        waited = self.limiter(c, wait=True).acquire()
        self.assertGreaterEqual(waited, 60.0)
        self.assertLess(waited, 61.0)

    def test_wait_mode_gives_up_after_max_wait(self):
        c = Clock()
        for _ in range(40):
            self.limiter(c).acquire()
        with self.assertRaises(gw.RateLimitExceeded):
            self.limiter(c, wait=True, max_wait=5).acquire()

    def test_run_enforces_limit(self):
        self.cfg["rate_limit_wait"] = False
        self.cfg["rate_limit_per_minute"] = 2
        c = Clock()
        opener = mock.Mock(side_effect=lambda *a, **k: model_reply("no diff here"))
        os.environ["NVIDIA_API_KEY"] = FAKE_NV_KEY
        for _ in range(2):
            rc, _, _ = self.run_gw(self.args("--task", "x", "--no-pr"), opener=opener, clock=c, sleep=c.sleep)
            self.assertEqual(rc, gw.EXIT_DIFF)
            c.t += 1
        rc, _, _ = self.run_gw(self.args("--task", "x", "--no-pr"), opener=opener, clock=c, sleep=c.sleep)
        self.assertEqual(rc, gw.EXIT_POLICY)
        self.assertEqual(opener.call_count, 2)
        self.assertEqual(self.ledger()[-1]["outcome"], "rate_limited")


class TestFlowAndLedger(Base):
    def test_dry_run_no_network(self):
        p = os.path.join(self.tmp, "d.diff")
        with open(p, "w") as f:
            f.write(GOOD_DIFF)
        opener = mock.Mock()
        with mock.patch.dict(os.environ, {"NVIDIA_API_KEY": FAKE_NV_KEY}):
            rc, out, err = self.run_gw(self.args("--task", "bump X", "--files", "Source/Foo.cs",
                                                 "--dry-run", "--diff-file", p), opener=opener)
        self.assertEqual(rc, 0, err)
        self.assertIn("would touch: Source/Foo.cs", out)
        self.assertNotIn(FAKE_NV_KEY, out + err)
        opener.assert_not_called()
        self.assertEqual(self.ledger()[-1]["outcome"], "dry_run")

    def test_diff_file_requires_dry_run(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            gw.parse_args(["--task", "x", "--diff-file", "a.diff"])

    def test_full_flow_pr_with_mocks_and_ledger_contents(self):
        remote = os.path.join(self.tmp, "remote.git")
        requests = []

        def opener(req, timeout=None):
            requests.append(req)
            if "integrate.api.nvidia.com" in req.full_url:
                return model_reply("```diff\n" + GOOD_DIFF + "```")
            if req.full_url == "https://api.github.com/repos/Holic-Jin/Feral-Inheritance/pulls":
                return FakeResp({"html_url": "https://github.com/Holic-Jin/Feral-Inheritance/pull/1"}, 201)
            raise AssertionError("unexpected URL " + req.full_url)

        task = "Raise Foo.X to 2 (unique-task-text-xyz)"
        with mock.patch.dict(os.environ, {"NVIDIA_API_KEY": FAKE_NV_KEY, "GITHUB_TOKEN": FAKE_GH_TOKEN}):
            rc, out, err = self.run_gw(self.args("--task", task, "--files", "Source/Foo.cs"), opener=opener)
        self.assertEqual(rc, 0, err)
        self.assertIn("pull/1", out)
        self.assertNotIn(FAKE_NV_KEY, out + err)
        self.assertNotIn(FAKE_GH_TOKEN, out + err)

        nv, pr = requests
        self.assertEqual(nv.full_url, "https://integrate.api.nvidia.com/v1/chat/completions")
        self.assertEqual(nv.get_header("Authorization"), "Bearer " + FAKE_NV_KEY)
        sent = json.loads(nv.data)
        self.assertEqual(sent["model"], "moonshotai/kimi-k2.6")
        self.assertIn("Source/Foo.cs", sent["messages"][1]["content"])
        self.assertEqual(pr.get_header("Authorization"), "Bearer " + FAKE_GH_TOKEN)
        self.assertNotIn(FAKE_NV_KEY, pr.data.decode())
        self.assertNotIn(FAKE_GH_TOKEN, nv.data.decode())
        self.assertTrue(json.loads(pr.data)["draft"])

        branches = subprocess.run(["git", "-C", remote, "branch", "--list", "model/*"],
                                  capture_output=True, text=True).stdout
        self.assertRegex(branches, r"model/t1-\d{8}T\d{6}Z")
        show = subprocess.run(["git", "-C", self.repo, "show", "--stat", "--format=%an", "HEAD"],
                              capture_output=True, text=True).stdout
        self.assertIn("feral-model-gateway", show)
        self.assertIn("Source/Foo.cs", show)

        led_path = os.path.join(self.repo, ".gateway", "calls.jsonl")
        with open(led_path, encoding="utf-8") as f:
            raw = f.read()
        rec = json.loads(raw.strip().splitlines()[-1])
        self.assertEqual(set(rec) - {"paths", "branch", "base_sha", "pr", "dry_run", "http_status", "duration_ms",
                                     "project"},
                         {"task_id", "timestamp", "model", "prompt_sha256", "prompt_tokens", "completion_tokens",
                          "total_tokens", "outcome"})
        self.assertEqual(rec["outcome"], "pr_opened")
        self.assertEqual(rec["project"], "feral-inheritance")
        self.assertEqual((rec["prompt_tokens"], rec["completion_tokens"], rec["total_tokens"]), (111, 22, 133))
        self.assertEqual(rec["prompt_sha256"], gw.prompt_sha256(sent["messages"]))
        self.assertRegex(rec["timestamp"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$")
        for forbidden in (FAKE_NV_KEY, FAKE_GH_TOKEN, "unique-task-text-xyz", "public const int", "Bearer"):
            self.assertNotIn(forbidden, raw)
        self.assertEqual(oct(os.stat(led_path).st_mode & 0o777), "0o600")
        self.assertEqual(rec["task_id"], "t1")

    def test_ledger_is_append_only(self):
        p = os.path.join(self.tmp, "l", "calls.jsonl")
        gw.append_ledger(p, {"task_id": "a", "outcome": "x", "prompt": "SHOULD-NOT-BE-LOGGED",
                             "response": "nope", "api_key": "nope", "messages": "nope"})
        gw.append_ledger(p, {"task_id": "b", "outcome": "y"})
        with open(p, encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual([json.loads(l)["task_id"] for l in lines], ["a", "b"])
        for l in lines:
            self.assertTrue(set(gw.LEDGER_REQUIRED) <= set(json.loads(l)))
        self.assertNotIn("SHOULD-NOT-BE-LOGGED", "".join(lines))
        self.assertNotIn("api_key", lines[0])

    def test_model_reply_touching_denied_path_is_not_applied(self):
        evil = GOOD_DIFF + diff_for(".github/workflows/test.yml")
        opener = mock.Mock(return_value=model_reply(evil))
        with mock.patch.dict(os.environ, {"NVIDIA_API_KEY": FAKE_NV_KEY}):
            rc, _, _ = self.run_gw(self.args("--task", "x", "--no-pr"), opener=opener)
        self.assertEqual(rc, gw.EXIT_DIFF)
        st = subprocess.run(["git", "-C", self.repo, "status", "--porcelain"], capture_output=True, text=True).stdout
        self.assertEqual(st.strip(), "")
        head = subprocess.run(["git", "-C", self.repo, "rev-parse", "--abbrev-ref", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        # the run's own fresh branch exists, but nothing was applied or committed on it
        self.assertTrue(head.startswith("model/t1-"), head)
        self.assertEqual(git_out(self.repo, "rev-parse", "HEAD"), git_out(self.repo, "rev-parse", "origin/main"))
        self.assertEqual(self.ledger()[-1]["outcome"], "rejected_denied_path")

    def test_api_http_error_logged(self):
        import urllib.error
        opener = mock.Mock(side_effect=urllib.error.HTTPError("u", 429, "Too Many", {}, None))
        with mock.patch.dict(os.environ, {"NVIDIA_API_KEY": FAKE_NV_KEY}):
            rc, out, err = self.run_gw(self.args("--task", "x", "--no-pr"), opener=opener)
        self.assertEqual(rc, gw.EXIT_API)
        self.assertNotIn(FAKE_NV_KEY, out + err)
        self.assertEqual(self.ledger()[-1]["outcome"], "api_http_429")

    def test_missing_key(self):
        rc, _, err = self.run_gw(self.args("--task", "x", "--no-pr"), opener=mock.Mock())
        self.assertEqual(rc, gw.EXIT_API)
        self.assertIn("NVIDIA_API_KEY is not set", err)


class TestUsageLogging(Base):
    def run_with_usage(self, payload_usage, omit=False):
        body = {"choices": [{"message": {"content": "no diff here"}}]}
        if not omit:
            body["usage"] = payload_usage
        opener = mock.Mock(return_value=FakeResp(body))
        with mock.patch.dict(os.environ, {"NVIDIA_API_KEY": FAKE_NV_KEY}):
            rc, out, err = self.run_gw(self.args("--task", "x", "--no-pr"), opener=opener)
        self.assertEqual(rc, gw.EXIT_DIFF)
        return self.ledger()[-1]

    def test_usage_recorded(self):
        rec = self.run_with_usage({"prompt_tokens": 1200, "completion_tokens": 340, "total_tokens": 1540})
        self.assertEqual((rec["prompt_tokens"], rec["completion_tokens"], rec["total_tokens"]), (1200, 340, 1540))
        self.assertEqual(rec["task_id"], "t1")
        self.assertEqual(rec["outcome"], "rejected_no_diff")
        self.assertRegex(rec["prompt_sha256"], r"^[0-9a-f]{64}$")

    def test_usage_missing_is_null(self):
        rec = self.run_with_usage(None, omit=True)
        self.assertEqual((rec["prompt_tokens"], rec["completion_tokens"], rec["total_tokens"]), (None, None, None))
        self.assertIn("prompt_tokens", rec)

    def test_usage_partial_or_garbage_is_null(self):
        rec = self.run_with_usage({"prompt_tokens": 10, "completion_tokens": "lots", "total_tokens": True})
        self.assertEqual((rec["prompt_tokens"], rec["completion_tokens"], rec["total_tokens"]), (10, None, None))
        rec = self.run_with_usage("not-a-dict")
        self.assertEqual(rec["total_tokens"], None)

    def test_refusal_logged_with_null_usage(self):
        rc, _, _ = self.run_gw(self.args("--project", "finismundi", "--task", "x"), opener=mock.Mock())
        rec = self.ledger()[-1]
        self.assertEqual(rec["outcome"], "refused_finismundi")
        for k in gw.LEDGER_REQUIRED:
            self.assertIn(k, rec)
        self.assertIsNone(rec["prompt_tokens"])

    def test_log_has_no_bodies_and_summary(self):
        self.run_with_usage({"prompt_tokens": 5, "completion_tokens": 6, "total_tokens": 11})
        path = os.path.join(self.repo, ".gateway", "calls.jsonl")
        with open(path, encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn("no diff here", raw)
        self.assertNotIn(FAKE_NV_KEY, raw)
        buf = io.StringIO()
        self.assertEqual(gw.summarize_log(path, buf), 1)
        self.assertIn("| t1 |", buf.getvalue())
        self.assertIn("| 5 | 6 | 11 |", buf.getvalue())

    def test_custom_log_file(self):
        p = os.path.join(self.tmp, "artifacts", "calls.jsonl")
        self.run_gw(self.args("--task", "x", "--dry-run", "--log-file", p))
        with open(p, encoding="utf-8") as f:
            self.assertEqual(json.loads(f.readline())["outcome"], "dry_run")


class TestFreshBranch(Base):
    def push_new_main_commit(self):
        other = os.path.join(self.tmp, "other")
        subprocess.run(["git", "clone", "-q", os.path.join(self.tmp, "remote.git"), other], check=True,
                       capture_output=True)
        with open(os.path.join(other, "Common", "Defs", "Bar.xml"), "a") as f:
            f.write("<!-- newer main -->\n")
        subprocess.run(["git", "-C", other, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                        "commit", "-qam", "newer"], check=True)
        subprocess.run(["git", "-C", other, "push", "-q", "origin", "main"], check=True, capture_output=True)
        return git_out(other, "rev-parse", "HEAD")

    def test_branch_name_format(self):
        self.assertEqual(gw.branch_name("fix-tox", 1793000000), "model/fix-tox-20261026T073320Z")
        for bad in ("", "Upper", "a/b", "../x", "-x", "x" * 49, "a b", "finismundi-1", "a..b"):
            with self.subTest(bad=bad):
                with self.assertRaises(gw.PolicyError):
                    gw.check_task_id(bad)
        gw.check_task_id("fi-0001_ab")

    def test_branch_from_latest_origin_main_not_local_head(self):
        newest = self.push_new_main_commit()   # local main is now behind origin/main
        c = Clock(1793000000.0)
        branch, base = gw.start_fresh_branch(self.repo, "t1", "main", c)
        self.assertEqual(branch, "model/t1-20261026T073320Z")
        self.assertEqual(base, newest)
        self.assertEqual(git_out(self.repo, "rev-parse", "HEAD"), newest)
        self.assertEqual(git_out(self.repo, "rev-parse", "--abbrev-ref", "HEAD"), branch)

    def test_never_reuses_local_or_remote_branch(self):
        c = Clock(1793000000.0)
        gw.start_fresh_branch(self.repo, "t1", "main", c)
        with self.assertRaises(gw.GitError) as cm:       # same name again (local)
            gw.start_fresh_branch(self.repo, "t1", "main", c)
        self.assertEqual(cm.exception.outcome, "branch_exists")
        subprocess.run(["git", "-C", self.repo, "push", "-q", "origin", "HEAD:refs/heads/model/t2-20261026T073320Z"],
                       check=True, capture_output=True)
        with self.assertRaises(gw.GitError) as cm:       # exists only on origin
            gw.start_fresh_branch(self.repo, "t2", "main", c)
        self.assertEqual(cm.exception.outcome, "branch_exists")
        c.t += 1                                          # a rerun one second later gets a new branch
        b, _ = gw.start_fresh_branch(self.repo, "t1", "main", c)
        self.assertEqual(b, "model/t1-20261026T073321Z")

    def test_dirty_worktree_refused(self):
        with open(os.path.join(self.repo, "Source", "Foo.cs"), "a") as f:
            f.write("// leftover from an interrupted run\n")
        with self.assertRaises(gw.GitError) as cm:
            gw.start_fresh_branch(self.repo, "t1", "main", Clock())
        self.assertEqual(cm.exception.outcome, "git_dirty_worktree")

    def test_failed_run_leaves_only_its_branch_and_rerun_is_clean(self):
        c = Clock(1793000000.0)
        evil = GOOD_DIFF + diff_for(".github/workflows/test.yml")
        opener = mock.Mock(return_value=model_reply(evil))
        with mock.patch.dict(os.environ, {"NVIDIA_API_KEY": FAKE_NV_KEY}):
            rc, _, err = self.run_gw(self.args("--task", "x", "--no-pr"), opener=opener, clock=c, sleep=c.sleep)
        self.assertEqual(rc, gw.EXIT_DIFF)
        self.assertIn("git branch -D model/t1-20261026T073320Z", err)
        self.assertEqual(git_out(self.repo, "branch", "--list", "model/*"), "* model/t1-20261026T073320Z")
        self.assertEqual(git_out(self.repo, "status", "--porcelain"), "")
        # cleanup, then rerun: new branch from origin/main, previous attempt not reused
        subprocess.run(["git", "-C", self.repo, "checkout", "-q", "main"], check=True)
        subprocess.run(["git", "-C", self.repo, "branch", "-D", "model/t1-20261026T073320Z"], check=True,
                       capture_output=True)
        c.t += 5
        opener = mock.Mock(return_value=model_reply(GOOD_DIFF))
        with mock.patch.dict(os.environ, {"NVIDIA_API_KEY": FAKE_NV_KEY}):
            rc, out, err = self.run_gw(self.args("--task", "x", "--no-pr"), opener=opener, clock=c, sleep=c.sleep)
        self.assertEqual(rc, 0, err)
        self.assertEqual(git_out(self.repo, "rev-parse", "--abbrev-ref", "HEAD"), "model/t1-20261026T073325Z")
        self.assertEqual(git_out(self.repo, "rev-parse", "HEAD~1"), git_out(self.repo, "rev-parse", "origin/main"))

    def test_dry_run_creates_no_branch_and_no_fetch(self):
        with mock.patch.object(gw, "start_fresh_branch", side_effect=AssertionError("no fetch in dry-run")):
            rc, out, err = self.run_gw(self.args("--task", "x", "--dry-run"), opener=mock.Mock())
        self.assertEqual(rc, 0, err)
        self.assertIn("branch(would be)=model/t1-", out)
        self.assertEqual(git_out(self.repo, "branch", "--list", "model/*"), "")

    def test_task_id_required(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            gw.parse_args(["--task", "x"])


class TestDispatchOutputs(Base):
    def test_only_opened_pr_is_exposed(self):
        branch, pr = gw.dispatch_outputs({
            "outcome": "pr_opened",
            "branch": "model/fix-tox-20261026T091500Z",
            "pr": "https://github.com/Holic-Jin/Feral-Inheritance/pull/12",
        })
        self.assertEqual(branch, "model/fix-tox-20261026T091500Z")
        self.assertEqual(pr, "12")

    def test_dry_run_local_commit_and_refusal_dispatch_nothing(self):
        for outcome in ("dry_run", "committed_local", "refused_task_id", "rejected_path"):
            with self.subTest(outcome=outcome):
                self.assertEqual(
                    gw.dispatch_outputs({"outcome": outcome, "branch": "model/t1-20261026T091500Z",
                                         "pr": "https://github.com/Holic-Jin/Feral-Inheritance/pull/3"}),
                    ("", ""))
        self.assertEqual(gw.dispatch_outputs(None), ("", ""))
        self.assertEqual(gw.dispatch_outputs({}), ("", ""))

    def test_pr_opened_without_number_or_with_newline_fails(self):
        with self.assertRaises(gw.GitError):
            gw.dispatch_outputs({"outcome": "pr_opened", "branch": "model/t1-20261026T091500Z", "pr": ""})
        with self.assertRaises(gw.GitError):
            gw.dispatch_outputs({"outcome": "pr_opened", "branch": "model/t1-20261026T091500Z\nmain",
                                 "pr": "https://github.com/Holic-Jin/Feral-Inheritance/pull/1"})

    def test_branch_name_matches_dispatch_pattern(self):
        samples = [
            gw.branch_name("fix-tox", 1793000000),
            gw.branch_name("t1", 1793000001),
            gw.branch_name("fi-0001_ab", 1793000000),
            gw.branch_name("a", 1793000000),
            gw.branch_name("a" * 48, 1793000000),
        ]
        for branch in samples:
            with self.subTest(branch=branch):
                self.assertRegex(branch, gw.MODEL_BRANCH_RE)
        for bad in ("", "main", "model/main", "model/Upper-20261026T073320Z",
                    "model/t1-20261026T073320Z;rm", "model/../../../etc", "refs/heads/main",
                    "model/t1-20261026T073320", "model/t1-20261026t073320Z",
                    "model/" + ("a" * 49) + "-20261026T073320Z"):
            with self.subTest(bad=bad):
                self.assertIsNone(gw.MODEL_BRANCH_RE.fullmatch(bad))


class TestWorkflowContract(Base):
    def test_model_call_dispatches_only_test_yml(self):
        path = os.path.join(REPO, ".github", "workflows", "model-call.yml")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("\npermissions: {}\n", text)
        self.assertEqual(text.count(gw.MODEL_BRANCH_RE.pattern), 1)
        self.assertIn('pat = re.compile(r"%s")' % gw.MODEL_BRANCH_RE.pattern, text)
        self.assertIn("/actions/workflows/test.yml/dispatches", text)
        self.assertNotIn("workflows/${{", text)
        gateway, dispatch = text.split("\n  dispatch-tests:\n", 1)
        self.assertIn("contents: write", gateway)
        self.assertIn("pull-requests: write", gateway)
        self.assertIn("NVIDIA_API_KEY: ${{ secrets.NVIDIA_API_KEY }}", gateway)
        self.assertIn("environment: nvidia-gateway", gateway)
        self.assertIn("if: github.ref == 'refs/heads/main'", gateway)
        self.assertIn("cancel-in-progress: false", text)
        self.assertNotIn("actions: write", gateway)
        self.assertNotIn("cat \"${RUNNER_TEMP}/gateway/calls.jsonl\"", text)
        self.assertIn("needs: gateway", dispatch)
        self.assertIn("success() && needs.gateway.outputs.branch != ''", dispatch)
        self.assertIn("actions: write", dispatch)
        self.assertNotIn("secrets.", dispatch)
        self.assertNotIn("actions/checkout", dispatch)
        self.assertNotIn("git init", dispatch)
        self.assertNotIn("NVIDIA", dispatch)
        header = dispatch.split("steps:", 1)[0]
        self.assertNotIn("contents:", header)
        self.assertNotIn("pull-requests:", header)

    def test_test_workflow_stays_read_only(self):
        path = os.path.join(REPO, ".github", "workflows", "test.yml")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("\npermissions:\n  contents: read\n", text)
        self.assertNotIn("pull_request_target:", text)
        self.assertNotIn("${{ secrets.", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("name: test (${{ matrix.rimworld }})", text)
        self.assertIn('rimworld: ["1.6", "1.7"]', text)


class TestQaFixes(Base):
    def test_finismundi_obfuscation_and_repo_paths(self):
        variants = (
            "FinisMundi",
            "finis-mundi",
            "Finis\u200bMundi",
            "f\u200bi\u200dn\u200bi\u200ds",
            "ＦｉｎｉｓＭｕｎｄｉ",
            "Fin1s",
            "f-i-n-i-s",
            "Fin1sMundi",
            "f-i-n-i-s-m-u-n-d-i",
            "Source/ＦｉｎｉｓＭｕｎｄｉ.cs",
        )
        for value in variants:
            with self.subTest(value=value):
                self.assertTrue(gw.mentions_finismundi(value), value)
        self.assertEqual(gw._fold_for_finismundi("Fin1s-Mund0"), "finis-mundo")
        self.assertFalse(gw.mentions_finismundi("finish the ability"))
        self.assertFalse(gw.mentions_finismundi("finish"))
        self.assertFalse(gw.mentions_finismundi("feral-inheritance"))
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "someone/Fin1s"}):
            with self.assertRaises(gw.PolicyError) as cm:
                gw.check_project("feral-inheritance", self.cfg)
            self.assertEqual(cm.exception.outcome, "refused_finismundi")
        with self.assertRaises(gw.PolicyError):
            gw.open_pr("acme/f-i-n-i-s", "t", "h", "main", "title", "body", opener=mock.Mock())
        with self.assertRaises(gw.DiffRejected):
            gw.check_write_path("Source/f-i-n-i-s.cs", self.cfg)
        with self.assertRaises(gw.DiffRejected):
            gw.check_write_path("Common/Defs/Fin1s.xml", self.cfg)

    def test_removed_double_dash_line_is_not_a_file_header(self):
        diff = (
            "diff --git a/Source/Foo.cs b/Source/Foo.cs\n"
            "--- a/Source/Foo.cs\n"
            "+++ b/Source/Foo.cs\n"
            "@@ -1,3 +1,2 @@\n"
            " kept\n"
            "--- a removed comment starting with two dashes\n"
            " kept-too\n"
        )
        self.assertEqual(gw.validate_diff(diff, self.cfg, self.repo), ["Source/Foo.cs"])

    def test_line_separators_cannot_hide_a_path(self):
        evil = (
            "diff --git a/Source/Foo.cs b/Source/Foo.cs\n"
            "--- a/Source/Foo.cs\n"
            "+++ b/Source/Foo.cs\n"
            "@@ -1 +1 @@\n"
            "-a\n"
            "+x\r@@ -1,3 +1,1 @@\n"
            "diff --git a/.github/workflows/evil.yml b/.github/workflows/evil.yml\n"
            "--- a/.github/workflows/evil.yml\n"
            "+++ b/.github/workflows/evil.yml\n"
        )
        with self.assertRaises(gw.DiffRejected) as cm:
            gw.validate_diff(evil, self.cfg, self.repo)
        self.assertEqual(cm.exception.outcome, "rejected_line_sep")
        for sep in ("\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"):
            with self.subTest(sep=repr(sep)):
                smuggled = (
                    "diff --git a/Source/Foo.cs b/Source/Foo.cs\n"
                    "--- a/Source/Foo.cs\n"
                    "+++ b/Source/Foo.cs\n"
                    "@@ -1 +1 @@\n"
                    "-a\n"
                    "+x" + sep + "@@ -1 +1 @@\n"
                    "diff --git a/.github/workflows/evil.yml b/.github/workflows/evil.yml\n"
                    "--- a/.github/workflows/evil.yml\n"
                    "+++ b/.github/workflows/evil.yml\n"
                )
                with self.assertRaises(gw.DiffRejected) as cm:
                    gw.validate_diff(smuggled, self.cfg, self.repo)
                self.assertEqual(cm.exception.outcome, "rejected_line_sep")
        crlf = GOOD_DIFF.replace("\n", "\r\n")
        self.assertEqual(gw.validate_diff(crlf, self.cfg, self.repo), ["Source/Foo.cs"])
        with self.assertRaises(gw.DiffRejected) as cm:
            gw.validate_diff(GOOD_DIFF + "not-a-header\n", self.cfg, self.repo)
        self.assertEqual(cm.exception.outcome, "rejected_hunk")
        secret = "SERVICE_KEY=" + "f8Kq2LmZ9xP4vR7tB1nW6yH3"
        for sep in ("\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"):
            with self.subTest(secret_sep=repr(sep)):
                self.assertTrue(gw.find_secrets("prefix" + sep + secret))

    def test_empty_hunk_line_is_context(self):
        diff = (
            "diff --git a/Source/Foo.cs b/Source/Foo.cs\n"
            "--- a/Source/Foo.cs\n"
            "+++ b/Source/Foo.cs\n"
            "@@ -1,3 +1,3 @@\n"
            " kept\n"
            "\n"
            " kept2\n"
        )
        self.assertEqual(gw.validate_diff(diff, self.cfg, self.repo), ["Source/Foo.cs"])

    def test_finismundi_confusables_and_word_boundaries(self):
        bypasses = (
            "F\u0456n\u0456s",          # Cyrillic і
            "F\u03b9n\u03b9s",          # Greek iota
            "f\u03b9n\u03b9\u03c3",     # Greek iota and sigma
            "fi\u0301nis",             # combining acute
            "f\u00ednis",              # precomposed i-acute, NFKD
            "fini5",
            "f!n!s",
            "f|n|s",
            "fini$",
        )
        for value in bypasses:
            with self.subTest(value=value):
                self.assertTrue(gw.mentions_finismundi(value), repr(value))
        for benign in ("Definis", "infinis", "the fin is sharp", "finish", "finish the ability"):
            with self.subTest(benign=benign):
                self.assertFalse(gw.mentions_finismundi(benign), benign)
        for folded in ("\u03dcinis", "\u03ddinis", "\ua730inis", "\u0192inis", "\ua799inis",
                       "F\u0131nis", "f\u026anis", "fini\ua731", "F1nls", "Finl5", "F!nls", "finl$"):
            with self.subTest(folded=folded):
                self.assertTrue(gw.mentions_finismundi(folded), repr(folded))
        for plain in ("flnis", "finls", "fl-nis", "nvidia/fl-nis", "Finls"):
            with self.subTest(plain=plain):
                self.assertFalse(gw.mentions_finismundi(plain), plain)
        self.assertTrue(gw.mentions_finismundi("Finis3Mundi"))
        self.assertTrue(gw.mentions_finismundi("FinisXMundi"))
        self.assertFalse(gw.mentions_finismundi("FinisXYZMundi"))

    def test_full_finismundi_matches_inside_other_words(self):
        embedded = (
            "xFinisMundi",
            "FinisMundiGame",
            "FinisMundi2",
            "2FinisMundi",
            "Holic-Jin/FinisMundiMod",
            "Source/FinisMundiPort.cs",
            "xfinismundi",
            "f i n i s m u n d i",
        )
        for value in embedded:
            with self.subTest(value=value):
                self.assertTrue(gw.mentions_finismundi(value), value)
                with self.assertRaises(gw.PolicyError) as cm:
                    gw.check_task_id(value)
                self.assertEqual(cm.exception.outcome, "refused_finismundi")
                with self.assertRaises(gw.PolicyError) as cm:
                    gw.check_model(value)
                if " " in value:
                    self.assertEqual(cm.exception.outcome, "refused_model")
                else:
                    self.assertEqual(cm.exception.outcome, "refused_finismundi")
                with self.assertRaises(gw.PolicyError) as cm:
                    gw.open_pr(value, "t", "h", "main", "title", "body", opener=mock.Mock())
                self.assertEqual(cm.exception.outcome, "refused_finismundi")
                with self.assertRaises(gw.GatewayError):
                    gw.check_write_path(value if "/" in value else "Source/" + value + ".cs", self.cfg)
                rc, _, err = self.run_gw(self.args("--task", "please " + value, "--dry-run"), opener=mock.Mock())
                self.assertEqual(rc, gw.EXIT_POLICY, err)
                self.assertEqual(self.ledger()[-1]["outcome"], "refused_finismundi")
                if " " not in value and "/" not in value:
                    with self.assertRaises(gw.DiffRejected):
                        gw.validate_diff(diff_for("Source/" + value + ".cs"), self.cfg, self.repo)
                elif " " not in value:
                    with self.assertRaises(gw.DiffRejected):
                        gw.validate_diff(diff_for(value), self.cfg, self.repo)
        with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "Holic-Jin/FinisMundiMod"}):
            with self.assertRaises(gw.PolicyError) as cm:
                gw.check_project("feral-inheritance", self.cfg)
            self.assertEqual(cm.exception.outcome, "refused_finismundi")
        rc, _, _ = self.run_gw(self.args("--task-id", "xfinismundi", "--task", "ok", "--dry-run"), opener=mock.Mock())
        self.assertEqual(rc, gw.EXIT_POLICY)
        self.assertEqual(self.ledger()[-1]["outcome"], "refused_finismundi")

    def test_no_newline_marker_allowed_index_secret_is_not(self):
        ok = (
            "diff --git a/Source/Foo.cs b/Source/Foo.cs\n"
            "--- a/Source/Foo.cs\n"
            "+++ b/Source/Foo.cs\n"
            "@@ -1 +1 @@\n"
            "-a\n"
            "+b\n"
            "\\ No newline at end of file\n"
        )
        self.assertEqual(gw.validate_diff(ok, self.cfg, self.repo), ["Source/Foo.cs"])
        with self.assertRaises(gw.DiffRejected):
            gw.validate_diff(ok.replace("\\ No newline at end of file", "\\ not the marker"), self.cfg, self.repo)
        hidden = diff_for("Source/Foo.cs") + "index 1111111..2222222 " + FAKE_NV_KEY + "\n" + diff_for("Source/Bar.cs")
        with self.assertRaises(gw.SecretDetected):
            gw.validate_diff(hidden, self.cfg, self.repo)
        before = (
            "diff --git a/Source/Foo.cs b/Source/Foo.cs\n"
            "index 1111111..2222222 " + FAKE_NV_KEY + "\n"
            "--- a/Source/Foo.cs\n"
            "+++ b/Source/Foo.cs\n"
            "@@ -1 +1 @@\n"
            "-a\n"
            "+b\n"
        )
        with self.assertRaises(gw.SecretDetected):
            gw.validate_diff(before, self.cfg, self.repo)
        binary = (
            "diff --git a/Source/Foo.cs b/Source/Foo.cs\n"
            "Binary files a/Source/Foo.cs and b/Source/Foo.cs differ\n"
        )
        with self.assertRaises(gw.DiffRejected) as cm:
            gw.validate_diff(binary, self.cfg, self.repo)
        self.assertEqual(cm.exception.outcome, "rejected_binary")
        with self.assertRaises(gw.DiffRejected):
            gw.validate_diff("Binary files not the git form\n" + diff_for("Source/Foo.cs"), self.cfg, self.repo)
        two = (
            "diff --git a/Source/Foo.cs b/Source/Foo.cs\n"
            "--- a/Source/Foo.cs\n"
            "+++ b/Source/Foo.cs\n"
            "@@ -2 +0,0 @@\n"
            "-a\n"
            "\\ No newline at end of file\n"
            "-b\n"
            "\\ No newline at end of file\n"
        )
        with self.assertRaises(gw.DiffRejected) as cm:
            gw.validate_diff(two, self.cfg, self.repo)
        self.assertEqual(cm.exception.outcome, "rejected_hunk")

    def test_ledger_failure_after_pr_names_the_pr_and_interrupts_propagate(self):
        def opener(req, timeout=None):
            if "integrate.api.nvidia.com" in req.full_url:
                return model_reply("```diff\n" + GOOD_DIFF + "```")
            if req.full_url.endswith("/pulls"):
                return FakeResp({"html_url": "https://github.com/Holic-Jin/Feral-Inheritance/pull/7"}, 201)
            raise AssertionError("unexpected URL " + req.full_url)

        secret = "ledger-" + FAKE_NV_KEY
        with mock.patch.dict(os.environ, {"NVIDIA_API_KEY": FAKE_NV_KEY, "GITHUB_TOKEN": FAKE_GH_TOKEN}):
            with mock.patch.object(gw, "append_ledger", side_effect=RuntimeError(secret)):
                rc, out, err = self.run_gw(self.args("--task", "bump X", "--files", "Source/Foo.cs"), opener=opener)
        self.assertNotEqual(rc, 0)
        self.assertIn("https://github.com/Holic-Jin/Feral-Inheritance/pull/7", err)
        self.assertIn("#7", err)
        self.assertIn("test.yml was not dispatched", err)
        self.assertNotIn(secret, out + err)
        self.assertNotIn("Traceback", err)

    def _interrupt_during(self, exc, where):
        def opener(req, timeout=None):
            if "integrate.api.nvidia.com" in req.full_url:
                if where == "model":
                    raise exc
                return model_reply("```diff\n" + GOOD_DIFF + "```")
            if req.full_url.endswith("/pulls"):
                if where == "pr":
                    raise exc
                return FakeResp({"html_url": "https://github.com/Holic-Jin/Feral-Inheritance/pull/4"}, 201)
            raise AssertionError("unexpected URL " + req.full_url)

        with mock.patch.dict(os.environ, {"NVIDIA_API_KEY": FAKE_NV_KEY, "GITHUB_TOKEN": FAKE_GH_TOKEN}):
            with self.assertRaises(type(exc)):
                self.run_gw(self.args("--task", "bump X", "--files", "Source/Foo.cs"), opener=opener)
        rows = self.ledger()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["outcome"], "interrupted")
        if where == "pr":
            self.assertEqual(rows[0]["prompt_tokens"], 111)
            self.assertEqual(rows[0]["completion_tokens"], 22)
        else:
            self.assertIsNone(rows[0]["prompt_tokens"])

    def test_interrupt_during_model_call_and_pr_is_logged_once(self):
        self._interrupt_during(KeyboardInterrupt(), "model")
        # fresh ledger: the helper above already consumed this repo's log, so use a new file via another run
        # after deleting the log
        os.remove(os.path.join(self.repo, ".gateway", "calls.jsonl"))
        self._interrupt_during(SystemExit(2), "model")
        os.remove(os.path.join(self.repo, ".gateway", "calls.jsonl"))
        self._interrupt_during(KeyboardInterrupt(), "pr")
        os.remove(os.path.join(self.repo, ".gateway", "calls.jsonl"))
        self._interrupt_during(SystemExit("cancel"), "pr")

    def test_model_segments_and_fullmatch(self):
        gw.check_model("moonshotai/kimi-k2.6")
        for bad in ("..", "../../x", "-rf", "a/..", "org/-rf", "a//b", "moonshotai/kimi-k2.6\n"):
            with self.subTest(bad=bad):
                with self.assertRaises(gw.PolicyError) as cm:
                    gw.check_model(bad)
                self.assertEqual(cm.exception.outcome, "refused_model")
        with self.assertRaises(gw.PolicyError):
            gw.check_task_id("t1\n")
        gw.check_task_id("t1")
        self.assertIsNone(gw.TASK_ID_RE.fullmatch("t1\n"))
        self.assertIsNone(gw.SAFE_PATH_RE.fullmatch("Source/Foo.cs\n"))

    def test_ledger_failure_stays_nonzero_without_secrets(self):
        secret = "ledger-" + FAKE_NV_KEY
        with mock.patch.object(gw, "append_ledger", side_effect=RuntimeError(secret)):
            rc, out, err = self.run_gw(self.args("--task", "x", "--dry-run"))
        self.assertNotEqual(rc, 0)
        self.assertNotIn(secret, out + err)
        self.assertNotIn("Traceback", err)
        self.assertIn("internal error", err)

    def test_socket_guard_blocks_non_loopback(self):
        blocked = ("192.0.2.1", 9)
        with self.assertRaises(AssertionError) as cm:
            socket.create_connection(blocked, timeout=0.2)
        self.assertIn("non-loopback", str(cm.exception))
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            with self.assertRaises(AssertionError) as cm:
                sock.connect(blocked)
            self.assertIn("non-loopback", str(cm.exception))
            with self.assertRaises(AssertionError):
                sock.connect_ex(blocked)
            with self.assertRaises(AssertionError):
                sock.sendto(b"x", blocked)
            with self.assertRaises(AssertionError):
                sock.sendmsg([b"x"], [], 0, blocked)
            with self.assertRaises(AssertionError):
                sock.bind(("0.0.0.0", 0))
            import _socket
            with self.assertRaises(AssertionError):
                _socket.socket.connect(sock, blocked)
        finally:
            sock.close()
        with self.assertRaises(AssertionError):
            socket.getaddrinfo("192.0.2.1", 9)
        self.assertTrue(_is_loopback(("127.0.0.1", 9)))
        self.assertTrue(_is_loopback(("localhost", 9)))
        self.assertFalse(_is_loopback(("example.com", 9)))
        self.assertFalse(_is_loopback(blocked))
        denied = subprocess.run(
            ["git", "ls-remote", "https://192.0.2.1/not-a-repo"],
            capture_output=True, text=True, timeout=10)
        self.assertNotEqual(denied.returncode, 0)

    def test_model_id_rules(self):
        gw.check_model("moonshotai/kimi-k2.6")
        for bad in ("", "has space", "a\nb", "a\rb", "bad\x00id", "x" * 101, "name$extra"):
            with self.subTest(bad=bad):
                with self.assertRaises(gw.PolicyError) as cm:
                    gw.check_model(bad)
                self.assertEqual(cm.exception.outcome, "refused_model")
        with self.assertRaises(gw.PolicyError) as cm:
            gw.check_model(FAKE_NV_KEY)
        self.assertEqual(cm.exception.outcome, "refused_secret")
        opener = mock.Mock()
        rc, _, err = self.run_gw(self.args("--task", "x", "--dry-run", "--model", "bad model"), opener=opener)
        self.assertEqual(rc, gw.EXIT_POLICY)
        self.assertNotIn("bad model", err)
        opener.assert_not_called()
        self.assertIsNone(self.ledger()[-1]["model"])
        rc, _, err = self.run_gw(self.args("--task", "x", "--dry-run", "--model", FAKE_NV_KEY), opener=opener)
        self.assertEqual(rc, gw.EXIT_POLICY)
        self.assertNotIn(FAKE_NV_KEY, err)
        self.assertNotIn(FAKE_NV_KEY, json.dumps(self.ledger()))

    def test_internal_error_is_logged_without_traceback(self):
        secret = "traceback-" + FAKE_NV_KEY
        with mock.patch.object(gw, "build_messages", side_effect=RuntimeError(secret)):
            rc, out, err = self.run_gw(self.args("--task", "x", "--dry-run"))
        self.assertEqual(rc, gw.EXIT_INTERNAL)
        self.assertEqual(self.ledger()[-1]["outcome"], "internal_error")
        self.assertNotIn(secret, out + err)
        self.assertNotIn("Traceback", err)
        self.assertNotIn("RuntimeError", err)

    def test_summary_cells_are_escaped(self):
        path = os.path.join(self.tmp, "calls.jsonl")
        gw.append_ledger(path, {
            "task_id": "t1",
            "outcome": "ok",
            "model": "evil|model\n<script>",
            "branch": "model/t1\r-x",
        })
        buf = io.StringIO()
        gw.summarize_log(path, buf)
        text = buf.getvalue()
        self.assertNotIn("<script>", text)
        self.assertIn("&lt;script&gt;", text)
        self.assertIn("evil\\|model", text)
        self.assertIn("```json", text)

    def test_pr_title_strips_controls_and_caps_length(self):
        title = gw.sanitize_pr_title("bad\r\ntitle\x00\u2028" + ("x" * 200))
        self.assertNotIn("\n", title)
        self.assertNotIn("\r", title)
        self.assertNotIn("\x00", title)
        self.assertNotIn("\u2028", title)
        self.assertLessEqual(len(title), gw.PR_TITLE_MAX)
        self.assertTrue(title.startswith("bad title"))
        captured = []

        def opener(req, timeout=None):
            captured.append(json.loads(req.data.decode()))
            return FakeResp({"html_url": "https://github.com/Holic-Jin/Feral-Inheritance/pull/9"})

        url = gw.open_pr("Holic-Jin/Feral-Inheritance", "t", "model/t1-20261026T000000Z", "main",
                         "line1\nline2", "body", opener=opener)
        self.assertEqual(url, "https://github.com/Holic-Jin/Feral-Inheritance/pull/9")
        self.assertEqual(captured[0]["title"], "line1 line2")

    def test_empty_or_redirect_response_is_not_a_pr_url(self):
        with self.assertRaises(gw.GitError) as cm:
            gw.open_pr("Holic-Jin/Feral-Inheritance", "t", "h", "main", "title", "body",
                       opener=lambda req, timeout=None: FakeResp({}, 200))
        self.assertEqual(cm.exception.outcome, "pr_no_url")
        with self.assertRaises(gw.GitError) as cm:
            gw.open_pr("Holic-Jin/Feral-Inheritance", "t", "h", "main", "title", "body",
                       opener=lambda req, timeout=None: FakeResp(
                           {"html_url": "https://github.com/Holic-Jin/Feral-Inheritance/pull/1"}, 302))
        self.assertEqual(cm.exception.outcome, "pr_redirect")

    def test_redirects_do_not_leak_authorization(self):
        marker_key = "local-marker-key"
        marker_token = "local-marker-token"

        class Origin(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length:
                    self.rfile.read(length)
                self.server.seen.append(self.headers.get("Authorization"))
                self.send_response(self.server.status_code)
                self.send_header("Location", self.server.location)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, fmt, *args):
                return

        class Sink(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0") or 0)
                payload = self.rfile.read(length) if length else b""
                self.server.seen.append((self.headers.get("Authorization"), payload))
                raw = b'{"choices":[{"message":{"content":"stolen"}}],"html_url":"http://sink/pull/1"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, fmt, *args):
                return

        def serve(handler):
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            server.seen = []
            server.thread = threading.Thread(target=server.serve_forever, daemon=True)
            server.thread.start()
            return server

        sink = serve(Sink)
        origin = serve(Origin)
        try:
            sink_url = "http://127.0.0.1:%d/sink" % sink.server_address[1]
            origin.location = sink_url
            origin_root = "http://127.0.0.1:%d" % origin.server_address[1]
            for code in (301, 302, 303):
                with self.subTest(code=code):
                    origin.status_code = code
                    origin.seen.clear()
                    sink.seen.clear()
                    cfg = dict(self.cfg, endpoint=origin_root + "/v1/chat/completions", request_timeout_seconds=5)
                    with self.assertRaises(gw.ApiError) as cm:
                        gw.call_model([{"role": "user", "content": "x"}], "moonshotai/kimi-k2.6", cfg, marker_key)
                    self.assertEqual(cm.exception.outcome, "api_redirect")
                    self.assertNotIn(marker_key, str(cm.exception))
                    self.assertEqual(sink.seen, [])
                    self.assertTrue(origin.seen)
                    self.assertTrue(all(marker_key in (auth or "") for auth in origin.seen))
                    origin.seen.clear()
                    with mock.patch.object(gw, "GITHUB_API", origin_root):
                        with self.assertRaises(gw.GitError) as cm:
                            gw.open_pr("Holic-Jin/Feral-Inheritance", marker_token, "model/t1-20261026T000000Z",
                                       "main", "title", "body")
                    self.assertEqual(cm.exception.outcome, "pr_redirect")
                    self.assertNotIn(marker_token, str(cm.exception))
                    self.assertEqual(sink.seen, [])
                    self.assertTrue(origin.seen)
                    self.assertTrue(all(marker_token in (auth or "") for auth in origin.seen))
        finally:
            origin.shutdown()
            sink.shutdown()
            origin.server_close()
            sink.server_close()
            origin.thread.join(timeout=2)
            sink.thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
