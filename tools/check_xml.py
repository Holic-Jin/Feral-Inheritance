#!/usr/bin/env python3
"""XML well-formedness check for every tracked *.xml (stdlib only).

- parses each file with xml.parsers.expat (no network, no external entities)
- rejects DOCTYPE / ENTITY declarations (XXE / entity-expansion guard)
- checks the RimWorld root element per folder: Defs/ -> <Defs>,
  Languages/**/Keyed|DefInjected -> <LanguageData>, Patches/ -> <Patch>
Exit 1 on any failure.
"""
import os
import subprocess
import sys
import xml.parsers.expat

EXPECTED_ROOT = [
    ("/Defs/", "Defs"),
    ("/Keyed/", "LanguageData"),
    ("/DefInjected/", "LanguageData"),
    ("/Patches/", "Patch"),
]


def list_xml(root):
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "-z", "--", "*.xml"], capture_output=True,
                             check=True).stdout.decode("utf-8")
        files = [f for f in out.split("\0") if f]
        if files:
            return files
    except (OSError, subprocess.CalledProcessError):
        pass
    found = []
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if x not in (".git", "bin", "obj", ".gateway")]
        for n in names:
            if n.lower().endswith(".xml"):
                found.append(os.path.relpath(os.path.join(d, n), root).replace(os.sep, "/"))
    return sorted(found)


def check_file(path, rel):
    roots = []

    def start(name, attrs):
        if not roots:
            roots.append(name)

    def forbid(*a, **k):
        raise ValueError("DOCTYPE/ENTITY declarations are not allowed")

    p = xml.parsers.expat.ParserCreate()
    p.StartElementHandler = start
    p.StartDoctypeDeclHandler = forbid
    p.EntityDeclHandler = forbid
    with open(path, "rb") as f:
        p.Parse(f.read(), True)
    probe = "/" + rel
    for marker, want in EXPECTED_ROOT:
        if marker in probe and roots and roots[0] != want:
            raise ValueError("root element <%s>, expected <%s>" % (roots[0], want))
    return roots[0] if roots else None


def main(argv):
    root = os.path.abspath(argv[1] if len(argv) > 1 else os.path.join(os.path.dirname(__file__), ".."))
    files = list_xml(root)
    bad = 0
    for rel in files:
        try:
            r = check_file(os.path.join(root, rel), rel)
            print("ok   %-70s <%s>" % (rel, r))
        except (xml.parsers.expat.ExpatError, ValueError, OSError) as e:
            bad += 1
            print("FAIL %s: %s" % (rel, e))
    print("%d XML files checked, %d failed" % (len(files), bad))
    return 1 if bad or not files else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
