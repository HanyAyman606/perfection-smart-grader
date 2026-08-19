# Building & selling Nexus Edge without giving out source

Two separate things happen here: (1) compiling the app so what you ship
is a binary, not `.py`/`.dart` source, and (2) making sure only people
who paid can run it, via the licensing system in `admin_dashboard/licensing/`.

## 1. One-time setup: generate your signing keypair

```bash
cd admin-dashboard
pip install cryptography
python3 tools/keygen.py
```

This writes `tools/private_key.pem` and prints a public key hex string.

- **`private_key.pem`**: yours forever, never shared, never committed,
  never bundled into the built app. Back it up somewhere durable (a
  password manager or encrypted drive) — if you lose it, you can never
  issue a new valid license for copies of the app already built with
  the matching public key, and you'd have to regenerate a new keypair
  and reship everyone a new build.
- **Public key hex**: paste it into
  `admin_dashboard/licensing/license_manager.py`, replacing the
  `PUBLIC_KEY_HEX` placeholder. This one *is* safe to ship — it can
  only verify signatures, not create them.

Both `tools/*.py` and `tools/private_key.pem` are already in
`.gitignore` — don't remove that.

## 2. Compile the PySide6 admin dashboard

Plain PyInstaller bundles compiled `.pyc` bytecode, which can be
decompiled back to fairly readable Python (`decompyle3`,
`uncompyle6`). **Nuitka** actually compiles Python to C, then to a real
native binary — much harder to reverse. Use Nuitka if the
scoring/grading logic is something you don't want copied:

```bash
pip install nuitka
python3 -m nuitka --standalone --onefile \
    --enable-plugin=pyside6 \
    --include-package=admin_dashboard \
    --output-dir=dist \
    admin_dashboard/main.py
```

Then per sale:

```bash
python3 tools/generate_license.py --licensee "Buyer Name" --out license.lic
```

Ship the buyer: the compiled binary from `dist/` + their `admin_dashboard/license.lic`.
Never ship `tools/`, `private_key.pem`, or any `.py` source.

See `tools/generate_license.py --help` for expiring or machine-locked
licenses.

## 3. Compile the Flutter client

Flutter already ships compiled ARM/native code by default — `.dart`
source is never included in a release build. Add `--obfuscate` to
rename classes/methods to meaningless symbols in the binary, making
reverse engineering harder:

```bash
cd edge_client
flutter build apk --release --obfuscate --split-debug-info=./debug-symbols
# or: flutter build linux --release --obfuscate --split-debug-info=./debug-symbols
```

Keep `debug-symbols/` yourself (privately, not shipped) — you'll need
it later to decode obfuscated crash stack traces. Don't commit it to
a public repo either.

The Flutter client currently has no license gate of its own — it only
talks to a dashboard on the same LAN, so gating the dashboard is what
actually controls who can run a session. Say the word if you also want
a license/activation check added client-side (e.g. for a variant sold
standalone, without the dashboard).

## 4. What this does and doesn't protect against

This raises the bar from "open the .py file in a text editor" to
"reverse-engineer a stripped native binary and patch out a signature
check" — it is not unbreakable DRM, and no offline scheme is. That's
consistent with how most desktop software you already buy actually
works.
